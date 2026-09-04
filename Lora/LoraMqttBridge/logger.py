"""Log-Level: debug / info / normal.

- normal: nur Errors und Start-/Stop-Meldungen
- info  : + jeder erfolgreich empfangene und gesendete Frame
- debug : + fehlerhafte Frames, IRQ-Dumps, ACK-Retries, Config-Details
"""

from __future__ import annotations

import logging
import sys
from typing import Literal

LogLevel = Literal["debug", "info", "normal"]

_LEVEL_MAP = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "normal": logging.WARNING,
}


def configure(level: LogLevel = "info") -> logging.Logger:
    if level not in _LEVEL_MAP:
        raise ValueError(f"unknown log_level {level!r}; use debug/info/normal")
    py_level = _LEVEL_MAP[level]
    root = logging.getLogger()
    root.setLevel(py_level)
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s.%(msecs)03d %(levelname)-5s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root.addHandler(handler)
    logging.getLogger("paho").setLevel(logging.WARNING)
    return logging.getLogger("lora_mqtt_bridge")
