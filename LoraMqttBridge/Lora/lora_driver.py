"""SX1262/SX126x-Treiber-Wrapper mit **IRQ-getriebenem** DIO1.

Zwei Backends automatisch erkannt:
- LoRaRF + rpi-lgpio  (Raspberry Pi OS Bookworm auf Pi Zero 2W / Pi 4/5)
- LoRaRF + gpiod v2   (HA-OS in Docker, /dev/gpiochip10 auf Pi 5)

Wenn LoRaRF's Event-Detect nicht zuverlässig arbeitet (siehe Proof-of-Concept),
setzen wir die DIO1-IRQs *im Chip*, hängen aber eine eigene GPIO-Line-Request
via `gpiod` an DIO1 und lauschen dort blockierend in einem Reader-Thread —
das ist der echte IRQ-Modus. Alternativ (Fallback) SPI-Polling.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Callable

from .config_loader import LoraConfig

log = logging.getLogger(__name__)


# IRQ-Bits (SX126x)
IRQ_TX_DONE = 0x0001
IRQ_RX_DONE = 0x0002
IRQ_PREAMBLE = 0x0004
IRQ_SYNC_VALID = 0x0008
IRQ_HEADER_VALID = 0x0010
IRQ_HEADER_ERR = 0x0020
IRQ_CRC_ERR = 0x0040
IRQ_TIMEOUT = 0x0200


@dataclass
class RxEvent:
    ok: bool
    payload: bytes
    rssi: int
    snr: float
    irq_bits: int


class LoraRadio:
    """Wrapper um LoRaRF.SX126x mit asyncio-freundlicher Rx-Queue."""

    def __init__(self, lora_cfg: LoraConfig):
        self.cfg = lora_cfg
        self._lora = None
        self._rx_queue: Queue[RxEvent] = Queue(maxsize=128)
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None
        self._tx_lock = threading.Lock()
        self._rxen_gpio = None
        self._gpio_backend: str | None = None

    # ------------------------------------------------------------ setup
    def open(self) -> None:
        from LoRaRF import SX126x  # noqa: WPS433 (runtime import)

        pins = self.cfg.pins
        self._lora = SX126x()

        # irq_pin: -1 => LoRaRF nicht auf DIO1 registrieren.
        # Wir übernehmen die IRQ-Zustellung selbst (siehe _start_dio1_reader).
        irq_pin = -1 if self.cfg.use_irq else -1

        log.info(
            "LoRa init: SPI %s:%s  RESET=BCM%d BUSY=BCM%d DIO1=BCM%d RXEN=BCM%d",
            pins.spi_bus, pins.spi_cs, pins.reset, pins.busy, pins.dio1, pins.rxen,
        )
        ok = self._lora.begin(
            pins.spi_bus, pins.spi_cs,
            pins.reset, pins.busy, irq_pin,
            pins.txen, pins.rxen,
        )
        if not ok:
            raise RuntimeError("SX126x konnte nicht initialisiert werden")

        self._lora.setDio2RfSwitch(True)
        if self.cfg.use_tcxo:
            self._lora.setDio3TcxoCtrl(self._lora.DIO3_OUTPUT_1_8,
                                       self._lora.TCXO_DELAY_10)

        self._lora.setFrequency(self.cfg.frequency_hz)
        if self.cfg.chip == "sx1261":
            self._lora.setTxPower(min(self.cfg.tx_power_dbm, 15),
                                  self._lora.TX_POWER_SX1261)
        else:
            self._lora.setTxPower(min(self.cfg.tx_power_dbm, 22),
                                  self._lora.TX_POWER_SX1262)
        self._lora.setLoRaModulation(
            self.cfg.spreading_factor,
            self.cfg.bandwidth_hz,
            self.cfg.coding_rate,
        )
        header_type = (
            self._lora.HEADER_EXPLICIT if self.cfg.header_explicit
            else self._lora.HEADER_IMPLICIT
        )
        self._lora.setLoRaPacket(header_type, self.cfg.preamble_length,
                                 255, self.cfg.crc_on)
        self._lora.setSyncWord(self.cfg.sync_word)

        # RXEN manuell steuern (DIO2 ist mit TXEN gebrückt).
        try:
            import RPi.GPIO as GPIO  # rpi-lgpio drop-in
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(pins.rxen, GPIO.OUT, initial=GPIO.LOW)
            self._rxen_gpio = GPIO
            log.debug("RXEN via RPi.GPIO (rpi-lgpio) initialisiert")
        except Exception as exc:  # pragma: no cover (nur auf Host relevant)
            log.warning("RPi.GPIO nicht verfügbar (%s) — RXEN wird ignoriert", exc)

        if self.cfg.use_irq:
            self._start_dio1_reader()
        else:
            self._start_poll_loop()

        self._start_rx()

    def close(self) -> None:
        self._stop.set()
        if self._reader:
            self._reader.join(timeout=2)
        try:
            if self._lora:
                self._lora.end()
        except Exception:
            pass

    # ------------------------------------------------------------ rxen
    def _rxen(self, high: bool) -> None:
        if not self._rxen_gpio:
            return
        self._rxen_gpio.output(
            self.cfg.pins.rxen,
            self._rxen_gpio.HIGH if high else self._rxen_gpio.LOW,
        )

    # ------------------------------------------------------------ IRQ / poll
    def _set_rx_irqs(self, sniff: bool = False) -> None:
        base = IRQ_RX_DONE | IRQ_HEADER_ERR | IRQ_CRC_ERR | IRQ_TIMEOUT
        if sniff:
            base |= IRQ_PREAMBLE | IRQ_SYNC_VALID | IRQ_HEADER_VALID
        self._lora.setDioIrqParams(base, base, 0x0000, 0x0000)

    def _start_rx(self) -> None:
        self._lora.standby()
        self._rxen(True)
        self._lora.clearIrqStatus(0x03FF)
        self._set_rx_irqs()
        self._lora.setRx(0xFFFFFF)  # continuous

    def _start_dio1_reader(self) -> None:
        """Startet einen Thread, der blockierend auf DIO1 lauscht (gpiod v2)."""
        try:
            import gpiod
        except ImportError:
            log.warning("gpiod nicht verfügbar — falle auf SPI-Polling zurück")
            self._start_poll_loop()
            return

        chip_path = self.cfg.pins.gpio_chip or "/dev/gpiochip0"
        pin = self.cfg.pins.dio1
        try:
            request = gpiod.request_lines(
                chip_path,
                consumer="lora-dio1",
                config={
                    pin: gpiod.LineSettings(
                        direction=gpiod.line.Direction.INPUT,
                        edge_detection=gpiod.line.Edge.RISING,
                        bias=gpiod.line.Bias.PULL_DOWN,
                    ),
                },
            )
        except Exception as exc:
            log.warning("DIO1 gpiod-request fehlgeschlagen (%s) — fallback poll", exc)
            self._start_poll_loop()
            return

        self._gpio_backend = f"gpiod:{chip_path}:{pin}"
        log.info("DIO1 IRQ via %s", self._gpio_backend)

        def _run() -> None:
            while not self._stop.is_set():
                try:
                    if request.wait_edge_events(timeout=0.5):
                        request.read_edge_events()
                        self._drain_irq()
                except Exception as exc:
                    log.exception("DIO1-Reader Fehler: %s", exc)
                    time.sleep(0.2)
            request.release()

        self._reader = threading.Thread(target=_run, name="dio1-irq", daemon=True)
        self._reader.start()

    def _start_poll_loop(self) -> None:
        self._gpio_backend = "poll"
        log.info("SPI-Poll-Loop alle %d ms", self.cfg.poll_interval_ms)

        def _run() -> None:
            interval = self.cfg.poll_interval_ms / 1000.0
            while not self._stop.is_set():
                self._drain_irq()
                time.sleep(interval)

        self._reader = threading.Thread(target=_run, name="lora-poll", daemon=True)
        self._reader.start()

    def _drain_irq(self) -> None:
        irq = self._lora.getIrqStatus()
        if not irq:
            return
        if irq & IRQ_RX_DONE:
            length, offset = self._lora.getRxBufferStatus()
            payload = bytes(self._lora.readBuffer(offset, length)) if length else b""
            rssi = self._lora.packetRssi()
            snr = self._lora.snr()
            crc_ok = not (irq & IRQ_CRC_ERR)
            hdr_ok = not (irq & IRQ_HEADER_ERR)
            self._lora.clearIrqStatus(0x03FF)
            try:
                self._rx_queue.put_nowait(
                    RxEvent(ok=crc_ok and hdr_ok, payload=payload,
                            rssi=rssi, snr=snr, irq_bits=irq)
                )
            except Exception:
                log.warning("RX-Queue voll, Frame verworfen")
        elif irq & (IRQ_HEADER_ERR | IRQ_CRC_ERR | IRQ_TIMEOUT):
            self._lora.clearIrqStatus(0x03FF)
            try:
                self._rx_queue.put_nowait(
                    RxEvent(ok=False, payload=b"", rssi=0, snr=0.0, irq_bits=irq)
                )
            except Exception:
                pass
        else:
            self._lora.clearIrqStatus(0x03FF)

    # ------------------------------------------------------------ TX/RX
    def send(self, payload: bytes, tx_timeout_s: float = 5.0) -> bool:
        """Sendet ein Paket. Blockiert bis TX_DONE/Timeout. Danach zurück in RX."""
        with self._tx_lock:
            self._lora.standby()
            self._rxen(False)
            self._lora.clearIrqStatus(0x03FF)
            self._lora.beginPacket()
            self._lora.write(list(payload), len(payload))
            self._lora.endPacket()

            t0 = time.time()
            while (time.time() - t0) < tx_timeout_s:
                irq = self._lora.getIrqStatus()
                if irq & IRQ_TX_DONE:
                    self._lora.clearIrqStatus(0x03FF)
                    self._start_rx()
                    return True
                if irq & IRQ_TIMEOUT:
                    self._lora.clearIrqStatus(0x03FF)
                    self._start_rx()
                    return False
                time.sleep(0.005)
            log.warning("TX-Timeout (%.1fs)", tx_timeout_s)
            self._start_rx()
            return False

    def get_rx(self, timeout: float = 0.1) -> RxEvent | None:
        try:
            return self._rx_queue.get(timeout=timeout)
        except Empty:
            return None

    @property
    def backend(self) -> str:
        return self._gpio_backend or "unknown"

    # ------------------------------------------------------------ helper
    def get_status(self) -> dict:
        return {
            "status": self._lora.getStatus() if self._lora else None,
            "irq": self._lora.getIrqStatus() if self._lora else None,
            "error": self._lora.getError() if self._lora else None,
            "backend": self.backend,
        }


# ----------------------------------------------------------------- factory
def build_radio(cfg: LoraConfig) -> LoraRadio:
    radio = LoraRadio(cfg)
    radio.open()
    return radio


# Hook, damit Tests die Radio-Klasse mocken können
_RadioFactory: Callable[[LoraConfig], LoraRadio] = build_radio
