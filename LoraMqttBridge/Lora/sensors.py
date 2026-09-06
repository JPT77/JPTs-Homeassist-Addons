"""Sensor-Reader für BMP280, AHT20 (I²C) und Wasserstand-ADC (MCP3008/ADS1115).

Jeder Sensor läuft in einem eigenen Reader-Thread und callback-t die Messwerte.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .config_loader import SensorSpec

log = logging.getLogger(__name__)

# Sensor-Callback-Typ: (sensor_name, field, value)
SensorCallback = Callable[[SensorSpec, str, float], None]


class SensorReader:
    def __init__(self, spec: SensorSpec, callback: SensorCallback):
        self.spec = spec
        self._cb = callback
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._backend = None

    def start(self) -> None:
        self._backend = _make_backend(self.spec)
        self._thread = threading.Thread(
            target=self._run, name=f"sensor-{self.spec.name}", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                readings = self._backend.read()
                for field, value in readings.items():
                    self._cb(self.spec, field, value)
            except Exception as exc:
                log.warning("Sensor %s Fehler: %s", self.spec.name, exc)
            time.sleep(self.spec.poll_interval_s)


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------
class _Bmp280:
    def __init__(self, spec: SensorSpec):
        import smbus2
        self._bus = smbus2.SMBus(spec.i2c_bus)
        self._addr = spec.i2c_address
        self._load_calibration()
        # Ctrl: temp x1, press x1, normal mode
        self._bus.write_byte_data(self._addr, 0xF4, 0x27)
        self._bus.write_byte_data(self._addr, 0xF5, 0xA0)

    def _load_calibration(self):
        data = self._bus.read_i2c_block_data(self._addr, 0x88, 24)
        import struct
        (self.dig_T1,) = struct.unpack("<H", bytes(data[0:2]))
        self.dig_T2, self.dig_T3 = struct.unpack("<hh", bytes(data[2:6]))
        (self.dig_P1,) = struct.unpack("<H", bytes(data[6:8]))
        self.dig_P2, self.dig_P3, self.dig_P4, self.dig_P5, \
            self.dig_P6, self.dig_P7, self.dig_P8, self.dig_P9 = \
            struct.unpack("<hhhhhhhh", bytes(data[8:24]))

    def read(self) -> dict[str, float]:
        raw = self._bus.read_i2c_block_data(self._addr, 0xF7, 6)
        adc_p = (raw[0] << 12) | (raw[1] << 4) | (raw[2] >> 4)
        adc_t = (raw[3] << 12) | (raw[4] << 4) | (raw[5] >> 4)
        var1 = (adc_t / 16384.0 - self.dig_T1 / 1024.0) * self.dig_T2
        var2 = ((adc_t / 131072.0 - self.dig_T1 / 8192.0) ** 2) * self.dig_T3
        t_fine = var1 + var2
        temperature = t_fine / 5120.0
        v1 = t_fine / 2.0 - 64000.0
        v2 = v1 * v1 * self.dig_P6 / 32768.0
        v2 += v1 * self.dig_P5 * 2.0
        v2 = v2 / 4.0 + self.dig_P4 * 65536.0
        v1 = (self.dig_P3 * v1 * v1 / 524288.0 + self.dig_P2 * v1) / 524288.0
        v1 = (1.0 + v1 / 32768.0) * self.dig_P1
        if v1 == 0:
            return {"temperature": temperature, "pressure": 0.0}
        p = 1048576.0 - adc_p
        p = (p - v2 / 4096.0) * 6250.0 / v1
        v1 = self.dig_P9 * p * p / 2147483648.0
        v2 = p * self.dig_P8 / 32768.0
        pressure = (p + (v1 + v2 + self.dig_P7) / 16.0) / 100.0  # hPa
        return {"temperature": round(temperature, 2), "pressure": round(pressure, 2)}


class _Aht20:
    def __init__(self, spec: SensorSpec):
        import smbus2
        self._bus = smbus2.SMBus(spec.i2c_bus)
        self._addr = spec.i2c_address
        time.sleep(0.04)
        self._bus.write_i2c_block_data(self._addr, 0xBE, [0x08, 0x00])
        time.sleep(0.02)

    def read(self) -> dict[str, float]:
        self._bus.write_i2c_block_data(self._addr, 0xAC, [0x33, 0x00])
        time.sleep(0.08)
        d = self._bus.read_i2c_block_data(self._addr, 0x00, 7)
        raw_h = ((d[1] << 12) | (d[2] << 4) | (d[3] >> 4))
        raw_t = (((d[3] & 0x0F) << 16) | (d[4] << 8) | d[5])
        humidity = raw_h * 100.0 / (1 << 20)
        temperature = raw_t * 200.0 / (1 << 20) - 50.0
        return {"humidity": round(humidity, 2), "temperature": round(temperature, 2)}


class _Mcp3008:
    def __init__(self, spec: SensorSpec):
        import spidev
        self._spi = spidev.SpiDev()
        # Pi hat i.d.R. spidev0.1 als 2. CS — für Sensor sinnvoll getrennt vom LoRa
        self._spi.open(0, 1)
        self._spi.max_speed_hz = 1_000_000
        self._channel = spec.channel
        self._vref = spec.vref
        self._field = spec.field or "voltage"

    def read(self) -> dict[str, float]:
        ch = self._channel & 0x07
        r = self._spi.xfer2([1, (8 + ch) << 4, 0])
        raw = ((r[1] & 0x03) << 8) | r[2]
        voltage = raw * self._vref / 1023.0
        return {self._field: round(voltage, 4)}


class _Ads1115:
    def __init__(self, spec: SensorSpec):
        import smbus2
        self._bus = smbus2.SMBus(spec.i2c_bus)
        self._addr = spec.i2c_address or 0x48
        self._channel = spec.channel & 0x03
        self._gain = spec.gain
        self._field = spec.field or "voltage"

    def read(self) -> dict[str, float]:
        mux = (0x4 + self._channel) << 12
        pga_bits = {6.144: 0x0000, 4.096: 0x0200, 2.048: 0x0400,
                    1.024: 0x0600, 0.512: 0x0800, 0.256: 0x0A00}
        pga_fs = min(pga_bits.keys(), key=lambda x: abs(x - self._gain))
        config = 0x8000 | mux | pga_bits[pga_fs] | 0x0100 | 0x0080 | 0x0003
        hi = (config >> 8) & 0xFF
        lo = config & 0xFF
        self._bus.write_i2c_block_data(self._addr, 0x01, [hi, lo])
        time.sleep(0.01)
        d = self._bus.read_i2c_block_data(self._addr, 0x00, 2)
        raw = (d[0] << 8) | d[1]
        if raw & 0x8000:
            raw -= 1 << 16
        voltage = raw * pga_fs / 32768.0
        return {self._field: round(voltage, 4)}


_BACKENDS = {
    "bmp280": _Bmp280,
    "aht20": _Aht20,
    "adc_mcp3008": _Mcp3008,
    "adc_ads1115": _Ads1115,
}


def _make_backend(spec: SensorSpec):
    if spec.kind == "system_status":
        from .sys_status import SystemStatus
        return SystemStatus(spec)
    cls = _BACKENDS.get(spec.kind)
    if cls is None:
        raise ValueError(f"unknown sensor kind: {spec.kind}")
    return cls(spec)
