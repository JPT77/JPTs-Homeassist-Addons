"""Log-Level: debug / info / normal / lora / internal.

Modi:
- info / lora     : Modus 1 — Zeigt NUR LoRA-Pakete (TX Frame, RX Frame, ACK) & Systemmeldungen. Subskribierte 1s MQTT-Nachrichten spammen nicht.
- debug / internal: Modus 2 — Zeigt die gesamte INTERNE VERARBEITUNG (sekündliche MQTT _on_mqtt Empfänge, Topic-Router entry Lookups, Forwarder und Nulleinspeisungs-Outputs).
- normal          : Nur Errors und wichtige Start-/Stop-Meldungen.
"""

from __future__ import annotations

import logging
import sys
from typing import Literal

LogLevel = Literal["debug", "info", "normal", "lora", "internal"]

_LEVEL_MAP = {
    "debug": logging.DEBUG,
    "internal": logging.DEBUG,
    "info": logging.INFO,
    "lora": logging.INFO,
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
