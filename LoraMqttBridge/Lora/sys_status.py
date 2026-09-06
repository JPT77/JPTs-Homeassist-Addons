"""System-Status Sensor: liest CPU/RAM/Temp/Uptime/RSSI vom Pi selbst."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


class SystemStatus:
    """Publisht einzelne Felder (NICHT zu einem Sensor gruppiert)."""

    def __init__(self, spec):
        self._boot_ts = self._read_boot()

    @staticmethod
    def _read_boot() -> float:
        try:
            return time.time() - float(Path("/proc/uptime").read_text().split()[0])
        except Exception:
            return time.time()

    def read(self) -> dict[str, float]:
        out: dict[str, float] = {}

        # CPU load 1min
        try:
            out["cpu_load_1m"] = round(os.getloadavg()[0], 2)
        except Exception:
            pass

        # CPU temperature
        for path in ("/sys/class/thermal/thermal_zone0/temp",):
            try:
                out["cpu_temperature"] = round(int(Path(path).read_text()) / 1000.0, 1)
                break
            except Exception:
                pass

        # Memory (MB used)
        try:
            info = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, _, rest = line.partition(":")
                info[key.strip()] = int(rest.strip().split()[0])
            total = info["MemTotal"]
            free = info.get("MemAvailable", info.get("MemFree", 0))
            out["memory_used_pct"] = round((total - free) * 100.0 / total, 1)
        except Exception:
            pass

        # Disk usage /
        try:
            st = os.statvfs("/")
            used = (st.f_blocks - st.f_bavail) * st.f_frsize
            total = st.f_blocks * st.f_frsize
            out["disk_used_pct"] = round(used * 100.0 / total, 1)
        except Exception:
            pass

        # Uptime seconds
        out["uptime_s"] = round(time.time() - self._boot_ts)

        # WiFi RSSI (AP or client link)
        try:
            r = subprocess.run(["iw", "dev", "wlan0", "station", "dump"],
                               capture_output=True, text=True, timeout=1)
            for line in r.stdout.splitlines():
                if "signal:" in line:
                    out["wifi_rssi"] = int(line.split()[1])
                    break
        except Exception:
            pass

        return out
