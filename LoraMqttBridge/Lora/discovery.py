"""HA MQTT Discovery Announcer.

Publisht für jedes RX/BIDIR-Topic bzw. jeden Sensor eine Discovery-Config
unter `homeassistant/<component>/<node>_<obj>/config`, damit die Entities
in Home Assistant ohne manuelle YAML erscheinen.
"""

from __future__ import annotations

import json
import logging

from .config_loader import Config, SensorSpec, TopicMap
from .mqtt_client import MqttBridge

log = logging.getLogger(__name__)

DISCOVERY_PREFIX = "homeassistant"

# Field -> Home Assistant sensor properties
_FIELD_META = {
    "temperature": {"device_class": "temperature", "unit_of_measurement": "°C",
                    "state_class": "measurement", "icon": "mdi:thermometer"},
    "humidity":    {"device_class": "humidity", "unit_of_measurement": "%",
                    "state_class": "measurement", "icon": "mdi:water-percent"},
    "pressure":    {"device_class": "atmospheric_pressure", "unit_of_measurement": "hPa",
                    "state_class": "measurement", "icon": "mdi:gauge"},
    "voltage":     {"device_class": "voltage", "unit_of_measurement": "V",
                    "state_class": "measurement", "icon": "mdi:sine-wave"},
    "wasserstand": {"unit_of_measurement": "V", "state_class": "measurement",
                    "icon": "mdi:water"},
    "soc":         {"device_class": "battery", "unit_of_measurement": "%",
                    "state_class": "measurement"},
}


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text.strip().lower()).strip("_")


def announce(cfg: Config, mqtt: MqttBridge) -> None:
    node_id = _slug(cfg.mqtt.client_id or "lora_bridge")
    device = {
        "identifiers": [f"lora_mqtt_bridge_{node_id}"],
        "name": f"LoRa MQTT Bridge ({cfg.role})",
        "manufacturer": "JPT77",
        "model": "SX1262 LoRa Bridge",
        "sw_version": "0.1.0",
    }
    announced = 0
    for topic in cfg.topics:
        if topic.direction in ("rx", "bidir"):
            announced += _announce_topic(node_id, device, topic, mqtt)
    for spec in cfg.sensors:
        if spec.mqtt_topic:
            announced += _announce_sensor(node_id, device, spec, mqtt)
    log.info("MQTT Discovery: %d Entities gepublisht", announced)


def _find_field_meta(field_name: str, topic_name: str) -> dict:
    """Find metadata matching the field name or topic name."""
    f_lower = field_name.lower()
    for key, meta in _FIELD_META.items():
        if key in f_lower:
            return meta
    t_lower = topic_name.lower()
    for key, meta in _FIELD_META.items():
        if key in t_lower:
            return meta
    return {}


def _announce_topic(node_id: str, device: dict, topic: TopicMap, mqtt: MqttBridge) -> int:
    if not topic.fields:
        obj_id = _slug(topic.mqtt_topic)
        unique_id = f"{node_id}_{obj_id}"
        payload = {
            "name": topic.mqtt_topic.replace("/", " ").title(),
            "state_topic": topic.mqtt_topic,
            "unique_id": unique_id,
            "device": device,
        }
        meta = _find_field_meta("", topic.mqtt_topic)
        payload.update(meta)
        cfg_topic = f"{DISCOVERY_PREFIX}/sensor/{unique_id}/config"
        mqtt.publish(cfg_topic, json.dumps(payload), qos=1, retain=True)
        return 1

    count = 0
    for field in topic.fields:
        if len(topic.fields) == 1:
            obj_id = _slug(topic.mqtt_topic)
            entity_name = topic.mqtt_topic.replace("/", " ").title()
        else:
            obj_id = f"{_slug(topic.mqtt_topic)}_{_slug(field.name)}"
            entity_name = f"{topic.mqtt_topic.replace('/', ' ').title()} {field.name.title()}"

        unique_id = f"{node_id}_{obj_id}"
        payload = {
            "name": entity_name,
            "state_topic": topic.mqtt_topic,
            "value_template": f"{{{{ value_json.{field.name} }}}}",
            "unique_id": unique_id,
            "device": device,
        }
        meta = _find_field_meta(field.name, topic.mqtt_topic)
        payload.update(meta)

        cfg_topic = f"{DISCOVERY_PREFIX}/sensor/{unique_id}/config"
        mqtt.publish(cfg_topic, json.dumps(payload), qos=1, retain=True)
        count += 1

    return count


def _announce_sensor(node_id: str, device: dict, spec: SensorSpec, mqtt: MqttBridge) -> int:
    # Ein Sensor kann mehrere Felder liefern (BMP280: temperature+pressure).
    fields = {
        "bmp280": ["temperature", "pressure"],
        "aht20": ["temperature", "humidity"],
        "adc_mcp3008": [spec.field],
        "adc_ads1115": [spec.field],
    }.get(spec.kind, [spec.field])

    count = 0
    for field in fields:
        # Topic wie im Sensor-Publisher gebildet
        topic = spec.mqtt_topic.format(name=spec.name, field=field)
        obj_id = f"{_slug(spec.name)}_{_slug(field)}"
        unique_id = f"{node_id}_{obj_id}"
        payload = {
            "name": f"{spec.name} {field}".replace("_", " "),
            "state_topic": topic,
            "unique_id": unique_id,
            "device": device,
            **_FIELD_META.get(field, {}),
        }
        cfg_topic = f"{DISCOVERY_PREFIX}/sensor/{unique_id}/config"
        mqtt.publish(cfg_topic, json.dumps(payload), qos=1, retain=True)
        count += 1
    return count
