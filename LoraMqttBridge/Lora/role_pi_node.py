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

import json
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

    def on_reading(spec, readings):
        for field, value in readings.items():
            if not field.startswith("_"):
                log.info("Sensor %s.%s = %s", spec.name, field, value)

        if "_timestamp" not in readings and "Time" not in readings:
            readings["_timestamp"] = time.time()

        reliable = spec.ack_req
        if spec.topic_id is not None:
            entry = bridge.router.topic_by_id(spec.topic_id)
            if entry is not None:
                reliable = reliable or bool(entry.reliable)
            bridge.send_mqtt_over_lora(spec.topic_id, readings, reliable=reliable)

        if spec.mqtt_topic:
            topic = spec.mqtt_topic.format(name=spec.name)
            payload = json.dumps(readings).encode("utf-8")
            mqtt.publish(topic, payload, qos=0, retain=True)

    num_sensors = len(cfg.sensors)
    for i, spec in enumerate(cfg.sensors):
        try:
            # Phasen-Offset (Staggering): Sensoren gleichmäßig über das Abfrageintervall verteilen
            # z.B. 3 Sensoren mit 300s Intervall -> initial_delay: 0s, 100s, 200s
            initial_delay = (i / num_sensors) * spec.poll_interval_s if num_sensors > 1 else 0.0
            r = SensorReader(spec, on_reading, initial_delay_s=initial_delay)
            r.start()
            readers.append(r)
            log.info("Sensor %s (%s) gestartet, poll=%.1fs (Initial-Offset=%.1fs)",
                     spec.name, spec.kind, spec.poll_interval_s, initial_delay)
        except Exception:
            log.exception("Konnte Sensor %s nicht starten", spec.name)
    return readers


def _install_signal_handler():
    import threading
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    return stop
