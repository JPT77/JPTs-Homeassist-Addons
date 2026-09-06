"""Pi-Node Rolle: startet alle vier Aufgaben in einem Prozess.

1. Battery-Relay: horcht auf zwei MQTT-Topics (WiFi) → sendet Änderungen
   als MQTT-Nachricht (WiFi) an den Akku.
2. MQTT→LoRa Forwarder: bereits durch die Bridge-Klasse abgedeckt
   (alle Topics mit direction tx/bidir werden über LoRa geschickt).
3. GPIO-Sensor-Publisher: liest BMP280/AHT20/ADC periodisch und
   publisht Werte über LoRa.
4. LoRa→MQTT Forwarder: durch die Bridge-Klasse abgedeckt
   (rx/bidir Topics werden auf WiFi-MQTT gespiegelt).
"""

from __future__ import annotations

import logging
import signal
import time

from .bridge import Bridge
from .config_loader import Config
from .discovery import announce as announce_discovery
from .lora_driver import build_radio
from .mqtt_client import MqttBridge
from .sensors import SensorReader

log = logging.getLogger(__name__)


def run(cfg: Config) -> int:
    radio = build_radio(cfg.lora)
    mqtt = MqttBridge(cfg.mqtt)
    mqtt.connect()
    bridge = Bridge(cfg, radio, mqtt)
    bridge.start()

    # MQTT Discovery + Web UI
    announce_discovery(cfg, mqtt)
    if getattr(cfg, "web_ui_port", 0):
        from .web_ui import register_bridge
        register_bridge(bridge)
        run_web_ui(port=cfg.web_ui_port)

    # --- 1. Battery relay -------------------------------------------------
    stop_battery = _install_battery_relay(cfg, mqtt)

    # --- 3. Sensor publisher ---------------------------------------------
    readers = _start_sensors(cfg, bridge, mqtt)

    log.info("pi_node läuft. Ctrl-C beendet.")
    stop = _install_signal_handler()
    try:
        while not stop.is_set():
            time.sleep(0.5)
    finally:
        log.info("Beende Pi-Node...")
        for r in readers:
            r.stop()
        stop_battery()
        bridge.stop()
        mqtt.stop()
        radio.close()
    return 0


# --------------------------------------------------------------------------
def _install_battery_relay(cfg: Config, mqtt: MqttBridge):
    br = cfg.battery_relay
    if not br.enabled or not br.sources:
        log.info("battery_relay deaktiviert")
        return lambda: None

    last: dict[str, bytes | None] = {s: None for s in br.sources}

    def on_msg(topic: str, payload: bytes):
        if topic not in last:
            return
        if last[topic] == payload:
            return
        last[topic] = payload
        try:
            value = payload.decode("utf-8", errors="replace")
        except Exception:
            value = repr(payload)
        out = br.payload_template.format(topic=topic, value=value).encode("utf-8")
        mqtt.publish(br.target, out, qos=1, retain=False)
        log.info("battery_relay: %s → %s (%r)", topic, br.target, value)

    # Zusätzlicher Handler kaskadiert die Bridge-eigenen on_message-Callbacks.
    prev = mqtt._on_message

    def chained(topic: str, payload: bytes):
        on_msg(topic, payload)
        if prev:
            prev(topic, payload)

    mqtt.set_on_message(chained)
    for src in br.sources:
        mqtt.subscribe(src, qos=1)

    def stop():
        mqtt.set_on_message(prev)

    return stop


def _start_sensors(cfg: Config, bridge: Bridge, mqtt: MqttBridge) -> list[SensorReader]:
    readers: list[SensorReader] = []

    def on_reading(spec, field, value):
        # Payload = einfacher UTF-8-Wert. Sensoren mit "mehrfeld" (BMP280 hat
        # temperature+pressure) senden je ein Frame pro Feld.
        payload = f"{value}".encode("utf-8")
        if spec.topic_id:
            bridge.send_mqtt_over_lora(spec.topic_id, payload, reliable=spec.ack_req)
        if spec.mqtt_topic:
            topic = spec.mqtt_topic.format(name=spec.name, field=field)
            mqtt.publish(topic, payload, qos=0, retain=True)
        log.info("Sensor %s.%s = %s", spec.name, field, value)

    for spec in cfg.sensors:
        try:
            r = SensorReader(spec, on_reading)
            r.start()
            readers.append(r)
            log.info("Sensor %s (%s) gestartet, poll=%.1fs",
                     spec.name, spec.kind, spec.poll_interval_s)
        except Exception:
            log.exception("Konnte Sensor %s nicht starten", spec.name)
    return readers


def _install_signal_handler():
    import threading
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    return stop
