from __future__ import annotations

import logging
import threading
from queue import Empty, Queue
from typing import Callable

from .config_loader import LoraConfig


log = logging.getLogger(__name__)


class RxEvent:
    """Kompatibles RX-Event für den Dummy-Treiber."""

    def __init__(
        self,
        ok: bool,
        payload: bytes = b"",
        rssi: int = 0,
        snr: float = 0.0,
        irq_bits: int = 0,
    ):
        self.ok = ok
        self.payload = payload
        self.rssi = rssi
        self.snr = snr
        self.irq_bits = irq_bits

    def __repr__(self) -> str:
        return (
            f"RxEvent(ok={self.ok!r}, "
            f"payload={self.payload!r}, "
            f"rssi={self.rssi!r}, "
            f"snr={self.snr!r}, "
            f"irq_bits=0x{self.irq_bits:04x})"
        )


class LoraRadio:
    """
    Dummy/Mock für den SX126x-Treiber.

    Es werden keinerlei GPIO-, SPI- oder LoRaRF-Zugriffe ausgeführt.
    Alle Aktionen werden lediglich geloggt.

    Die öffentliche API entspricht weitgehend dem echten LoraRadio.
    """

    def __init__(self, lora_cfg: LoraConfig):
        self.cfg = lora_cfg

        self._rx_queue: Queue[RxEvent] = Queue(maxsize=128)
        self._stop = threading.Event()
        self._opened = False
        self._gpio_backend = "mock"
        self._tx_lock = threading.Lock()

        log.info("Mock-LoraRadio erstellt")

    # ------------------------------------------------------------ setup

    def open(self) -> None:
        """Dummy-Initialisierung."""

        if self._opened:
            log.warning("Mock-LoraRadio.open(): bereits geöffnet")
            return

        self._opened = True

        pins = self.cfg.pins

        log.info(
            "MOCK LoRa init: SPI=%s:%s RESET=BCM%d BUSY=BCM%d "
            "DIO1=BCM%d RXEN=BCM%d",
            pins.spi_bus,
            pins.spi_cs,
            pins.reset,
            pins.busy,
            pins.dio1,
            pins.rxen,
        )

        log.info(
            "MOCK LoRa config: frequency=%s Hz, SF=%s, BW=%s Hz, "
            "CR=%s, TX power=%s dBm",
            self.cfg.frequency_hz,
            self.cfg.spreading_factor,
            self.cfg.bandwidth_hz,
            self.cfg.coding_rate,
            self.cfg.tx_power_dbm,
        )

        log.info(
            "MOCK LoRa ready — keine Hardware wird angesprochen"
        )

    def close(self) -> None:
        """Dummy-Shutdown."""

        if not self._opened:
            log.debug("Mock-LoraRadio.close(): bereits geschlossen")
            return

        log.info("MOCK LoRa close()")

        self._stop.set()
        self._opened = False

    # ------------------------------------------------------------ rxen

    def _rxen(self, high: bool) -> None:
        """Dummy RXEN GPIO."""

        log.debug(
            "MOCK RXEN -> %s",
            "HIGH" if high else "LOW",
        )

    # ------------------------------------------------------------ RX

    def _start_rx(self) -> None:
        """Dummy RX-Start."""

        log.debug("MOCK start RX")

    def _start_dio1_reader(self) -> None:
        """Dummy DIO1 IRQ-Reader."""

        log.debug("MOCK DIO1 IRQ reader gestartet")

    def _start_poll_loop(self) -> None:
        """Dummy Polling-Loop."""

        log.debug("MOCK SPI-Polling gestartet")

    def _drain_irq(self) -> None:
        """Dummy IRQ-Verarbeitung."""

        log.debug("MOCK drain IRQ")

    # ------------------------------------------------------------ TX

    def send(
        self,
        payload: bytes,
        tx_timeout_s: float = 5.0,
    ) -> bool:
        """
        Dummy-Senden.

        Gibt immer True zurück, sofern der Treiber geöffnet wurde.
        """

        with self._tx_lock:
            if not self._opened:
                log.warning(
                    "MOCK send(): Radio ist nicht geöffnet"
                )
                return False

            log.info(
                "MOCK TX: %d Bytes: %s",
                len(payload),
                payload.hex(" "),
            )

            log.debug(
                "MOCK TX timeout=%s s",
                tx_timeout_s,
            )

            # Keine echte Übertragung.
            return True

    def get_rx(
        self,
        timeout: float = 0.1,
    ) -> RxEvent | None:
        """
        Liest ein eventuell künstlich eingestelltes RX-Event.

        Normalerweise gibt der Mock None zurück.
        """

        try:
            event = self._rx_queue.get(timeout=timeout)

            log.info(
                "MOCK RX event: %r",
                event,
            )

            return event

        except Empty:
            return None

    # ------------------------------------------------------------ Mock helper

    def inject_rx(
        self,
        payload: bytes,
        *,
        ok: bool = True,
        rssi: int = 0,
        snr: float = 0.0,
        irq_bits: int = 0,
    ) -> None:
        """
        Erzeugt künstlich ein RX-Event.

        Nützlich für Tests:

            radio.inject_rx(b"hello")

        Danach liefert:

            radio.get_rx()

        das künstliche Paket.
        """

        event = RxEvent(
            ok=ok,
            payload=payload,
            rssi=rssi,
            snr=snr,
            irq_bits=irq_bits,
        )

        log.info(
            "MOCK inject RX: %r",
            event,
        )

        try:
            self._rx_queue.put_nowait(event)
        except Exception:
            log.warning(
                "MOCK RX-Queue voll, Event verworfen"
            )

    def clear_rx(self) -> None:
        """Leert die Dummy-RX-Queue."""

        count = 0

        while True:
            try:
                self._rx_queue.get_nowait()
                count += 1
            except Empty:
                break

        log.debug(
            "MOCK RX-Queue geleert (%d Events)",
            count,
        )

    # ------------------------------------------------------------ properties

    @property
    def backend(self) -> str:
        return self._gpio_backend

    # ------------------------------------------------------------ status

    def get_status(self) -> dict:
        """Liefert einen hardwarefreien Dummy-Status."""

        status = {
            "status": "MOCK",
            "irq": 0,
            "error": 0,
            "backend": self.backend,
            "opened": self._opened,
        }

        log.debug(
            "MOCK status: %s",
            status,
        )

        return status


# ----------------------------------------------------------------- factory

def build_radio(cfg: LoraConfig) -> LoraRadio:
    """
    Factory für den Dummy-Treiber.
    """

    log.info("MOCK build_radio()")

    radio = LoraRadio(cfg)
    radio.open()

    return radio


# Hook wie beim echten Treiber
_RadioFactory: Callable[[LoraConfig], LoraRadio] = build_radio
