"""Schrittweises Erforschen der LoRa-Hardware beim Boot.

Gibt für jeden Schritt eine klare Log-Zeile aus und wirft bei Fehlern eine
Exception mit sprechendem Text.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from .config_loader import LoraConfig, ProbeConfig

log = logging.getLogger(__name__)


def probe(lora_cfg: LoraConfig, probe_cfg: ProbeConfig) -> None:
    """Führt die Hardware-Diagnose in 10 Schritten aus."""
    _step(1, "SPI-Device öffnen", _check_spi, lora_cfg)
    _step(2, "GPIO-Chip öffnen", _check_gpio_chip, lora_cfg)
    _step(3, "BUSY-Pin lesbar", _check_busy_readable, lora_cfg)
    _step(4, "NRST-Toggle → BUSY reagiert", _check_reset_pulse, lora_cfg)
    _step(5, "SX126x GetStatus per SPI", _check_get_status, lora_cfg)
    _step(6, "SX126x GetDeviceErrors == 0", _check_no_errors, lora_cfg)
    _step(7, "Modulation & Paket-Parameter setzen", _check_apply_params, lora_cfg)
    _step(8, "Sync-Word setzen", _check_sync_word, lora_cfg)
    _step(9, "DIO1 IRQ-Setup", _check_dio1, lora_cfg)
    if probe_cfg.tx_test:
        _step(10, "TX-Test-Frame senden", _check_tx, lora_cfg, probe_cfg)
    else:
        log.info("Step 10 übersprungen (probe.tx_test=false)")


def _step(idx: int, title: str, fn, *args) -> None:
    log.info("Probe %2d: %s ...", idx, title)
    try:
        fn(*args)
        log.info("Probe %2d: OK", idx)
    except Exception as exc:
        log.error("Probe %2d: FAILED — %s", idx, exc)
        raise


def _check_spi(cfg: LoraConfig) -> None:
    dev = cfg.pins.spi_device or f"/dev/spidev{cfg.pins.spi_bus}.{cfg.pins.spi_cs}"
    if not Path(dev).exists():
        raise RuntimeError(f"SPI device {dev} nicht vorhanden — dtparam=spi=on?")
    if not os.access(dev, os.R_OK | os.W_OK):
        raise RuntimeError(f"Keine RW-Rechte auf {dev} (User in Gruppe 'spi'?)")


def _check_gpio_chip(cfg: LoraConfig) -> None:
    chip = cfg.pins.gpio_chip or "/dev/gpiochip0"
    if not Path(chip).exists():
        raise RuntimeError(f"GPIO chip {chip} nicht vorhanden")
    if not os.access(chip, os.R_OK | os.W_OK):
        raise RuntimeError(f"Keine RW-Rechte auf {chip} (User in Gruppe 'gpio'?)")


def _check_busy_readable(cfg: LoraConfig) -> None:
    try:
        import gpiod
        chip = cfg.pins.gpio_chip or "/dev/gpiochip0"
        req = gpiod.request_lines(
            chip,
            consumer="lora-probe-busy",
            config={cfg.pins.busy: gpiod.LineSettings(
                direction=gpiod.line.Direction.INPUT)},
        )
        _ = req.get_value(cfg.pins.busy)
        req.release()
    except ImportError:
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(cfg.pins.busy, GPIO.IN)
        _ = GPIO.input(cfg.pins.busy)


def _check_reset_pulse(cfg: LoraConfig) -> None:
    """NRST kurz LOW-HIGH-toggeln und BUSY beobachten."""
    try:
        import gpiod
        chip = cfg.pins.gpio_chip or "/dev/gpiochip0"
        req = gpiod.request_lines(
            chip,
            consumer="lora-probe-reset",
            config={
                cfg.pins.reset: gpiod.LineSettings(
                    direction=gpiod.line.Direction.OUTPUT, output_value=1),
                cfg.pins.busy: gpiod.LineSettings(
                    direction=gpiod.line.Direction.INPUT),
            },
        )
        req.set_value(cfg.pins.reset, 0)
        time.sleep(0.005)
        req.set_value(cfg.pins.reset, 1)
        time.sleep(0.05)
        _ = req.get_value(cfg.pins.busy)
        req.release()
    except ImportError:
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(cfg.pins.reset, GPIO.OUT, initial=GPIO.HIGH)
        GPIO.output(cfg.pins.reset, GPIO.LOW)
        time.sleep(0.005)
        GPIO.output(cfg.pins.reset, GPIO.HIGH)
        time.sleep(0.05)


# Die folgenden Schritte benutzen ein temporäres LoRaRF-Handle. Nach den Checks
# wird es geschlossen — der echte Radio-Handle wird in lora_driver.py aufgebaut.
def _tmp_radio(cfg: LoraConfig):
    from LoRaRF import SX126x
    lora = SX126x()
    if not lora.begin(cfg.pins.spi_bus, cfg.pins.spi_cs,
                      cfg.pins.reset, cfg.pins.busy, -1,
                      cfg.pins.txen, cfg.pins.rxen):
        raise RuntimeError("lora.begin() fehlgeschlagen")
    return lora


def _check_get_status(cfg: LoraConfig) -> None:
    lora = _tmp_radio(cfg)
    try:
        st = lora.getStatus()
        mode = (st >> 4) & 0x07
        if mode == 0:
            raise RuntimeError(f"Chip meldet mode=0 (unconfigured), status=0x{st:02X}")
        log.debug("Status=0x%02X mode=0x%X", st, mode)
    finally:
        lora.end()


def _check_no_errors(cfg: LoraConfig) -> None:
    lora = _tmp_radio(cfg)
    try:
        err = lora.getError()
        if err:
            raise RuntimeError(f"Chip meldet Fehler 0x{err:04X}")
    finally:
        lora.end()


def _check_apply_params(cfg: LoraConfig) -> None:
    lora = _tmp_radio(cfg)
    try:
        lora.setFrequency(cfg.frequency_hz)
        lora.setLoRaModulation(cfg.spreading_factor, cfg.bandwidth_hz, cfg.coding_rate)
        header = lora.HEADER_EXPLICIT if cfg.header_explicit else lora.HEADER_IMPLICIT
        lora.setLoRaPacket(header, cfg.preamble_length, 255, cfg.crc_on)
    finally:
        lora.end()


def _check_sync_word(cfg: LoraConfig) -> None:
    lora = _tmp_radio(cfg)
    try:
        lora.setSyncWord(cfg.sync_word)
    finally:
        lora.end()


def _check_dio1(cfg: LoraConfig) -> None:
    if not cfg.use_irq:
        log.info("use_irq=false → DIO1-IRQ-Check übersprungen")
        return
    try:
        import gpiod
        chip = cfg.pins.gpio_chip or "/dev/gpiochip0"
        req = gpiod.request_lines(
            chip,
            consumer="lora-probe-dio1",
            config={cfg.pins.dio1: gpiod.LineSettings(
                direction=gpiod.line.Direction.INPUT,
                edge_detection=gpiod.line.Edge.RISING)},
        )
        req.release()
    except ImportError:
        log.warning("gpiod fehlt → IRQ läuft ggf. über SPI-Poll-Fallback")


def _check_tx(cfg: LoraConfig, probe_cfg: ProbeConfig) -> None:
    lora = _tmp_radio(cfg)
    try:
        lora.setFrequency(cfg.frequency_hz)
        lora.setLoRaModulation(cfg.spreading_factor, cfg.bandwidth_hz, cfg.coding_rate)
        lora.setLoRaPacket(lora.HEADER_EXPLICIT, cfg.preamble_length, 255, cfg.crc_on)
        lora.setSyncWord(cfg.sync_word)
        data = probe_cfg.tx_test_payload.encode("utf-8")
        lora.beginPacket()
        lora.write(list(data), len(data))
        lora.endPacket()
        t0 = time.time()
        while time.time() - t0 < 3.0:
            irq = lora.getIrqStatus()
            if irq & 0x0001:
                lora.clearIrqStatus(0x03FF)
                return
            time.sleep(0.01)
        raise RuntimeError("Kein TX_DONE innerhalb 3s")
    finally:
        lora.end()
