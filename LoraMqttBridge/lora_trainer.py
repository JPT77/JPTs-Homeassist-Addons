#!/usr/bin/env python3
"""LoRa SX1262 Trainer entry point for Home Assistant Add-on and Raspberry Pi nodes."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure package path is on sys.path
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if "/app" not in sys.path and Path("/app").exists():
    sys.path.insert(0, "/app")

from Lora.lora_trainer import main

if __name__ == "__main__":
    sys.exit(main())
