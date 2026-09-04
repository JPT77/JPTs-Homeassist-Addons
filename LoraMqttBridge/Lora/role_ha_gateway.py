"""HA-Gateway Rolle: LoRa <-> HA-interner MQTT-Broker.

Sensoren gibt es hier nicht; auch kein Battery-Relay — beides läuft am Pi.
"""

from __future__ import annotations

import logging
import signal
import time

from .bridge import Bridge
from .config_loader import Config
from .lora_driver import build_radio
from .mqtt_client import MqttBridge

log = logging.getLogger(__name__)


def run(cfg: Config) -> int:
    radio = build_radio(cfg.lora)
    mqtt = MqttBridge(cfg.mqtt)
    mqtt.connect()
    bridge = Bridge(cfg, radio, mqtt)
    bridge.start()

    log.info("ha_gateway läuft. Backend=%s", radio.backend)

    import threading
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    try:
        while not stop.is_set():
            time.sleep(0.5)
    finally:
        log.info("Beende HA-Gateway...")
        bridge.stop()
        mqtt.stop()
        radio.close()
    return 0
