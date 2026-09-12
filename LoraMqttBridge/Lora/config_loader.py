"""Config loader for YAML file (pi_node) and HA-Addon Options (ha_gateway).

Supports two schema flavours for topics.yaml:

Legacy flat schema (still accepted):
    - id: 1
      mqtt_topic: "foo/bar"
      direction: tx | rx | bidir
      qos: 1
      retained: true
      fields: [...]

New nested schema (as used in the current PiNode/config.yaml + topics.yaml):
    - id: 1
      name: my_topic
      mqtt_topic: "foo/bar"          # optional; may also be under mqtt.topic
      direction: to_gateway | from_gateway | bidir
      mqtt:
        topic: "foo/bar"
        qos: 1
        retained: true
      lora:
        reliable: true
      fields: [...]
      transform:
        mqtt2lora: { field: "<jq>" }
        lora2mqtt: { field: "<jq>" }

The direction values `to_gateway` and `from_gateway` describe the semantic
flow (node -> gateway or gateway -> node) independent of the role.  The
`role_direction()` helper on :class:`TopicMap` resolves the semantic
direction to the local flow (`tx`, `rx`, or `bidir`) for a given role.

The new `mqtt_outputs` section is also parsed: it lets the pi_node
compute a control value out of one or more subscription streams and
publish it to a local MQTT topic (see :class:`MqttOutput`).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Dataclasses
# --------------------------------------------------------------------------
@dataclass
class MqttConfig:
    host: str = "127.0.0.1"
    port: int = 1883
    username: str | None = None
    password: str | None = None
    tls: bool = False
    client_id: str = "lora-bridge"
    keepalive: int = 60


@dataclass
class LoraPins:
    spi_bus: int = 0
    spi_cs: int = 0
    spi_device: str | None = None     # z. B. /dev/spidev10.0 (HA OS Pi5)
    gpio_chip: str | None = None      # z. B. /dev/gpiochip10 (HA OS Pi5)
    reset: int = 24
    busy: int = 23
    dio1: int = 25
    rxen: int = 22
    txen: int = -1


@dataclass
class LoraConfig:
    chip: str = "sx1262"              # sx1261 / sx1262
    frequency_hz: int = 868_000_000
    spreading_factor: int = 7
    bandwidth_hz: int = 125_000
    coding_rate: int = 6              # 5..8
    tx_power_dbm: int = 22
    preamble_length: int = 8
    crc_on: bool = True
    header_explicit: bool = True
    sync_word: int = 0x1424
    use_tcxo: bool = False
    use_irq: bool = True              # DIO1 als GPIO-IRQ; False => SPI-Poll
    poll_interval_ms: int = 20        # Fallback-Poll-Intervall
    pins: LoraPins = field(default_factory=LoraPins)


@dataclass
class AckConfig:
    timeout_ms: int = 800
    max_retries: int = 4
    backoff_factor: float = 1.6


@dataclass
class FieldSpec:
    name: str
    type: str = "float"


@dataclass
class TopicTransform:
    """jq expressions to convert between MQTT JSON payloads and the ordered
    binary field-dict used for LoRa.

    Each mapping is `{binary_field_name: jq_expression}` for `mqtt2lora`
    and `{mqtt_key: jq_expression}` for `lora2mqtt` (mqtt2lora yields the
    field values consumed by :class:`PayloadCodec`, lora2mqtt yields the
    JSON object that is published back to MQTT).
    """
    mqtt2lora: dict[str, Any] = field(default_factory=dict)
    lora2mqtt: dict[str, Any] = field(default_factory=dict)


@dataclass
class TopicMap:
    id: int
    mqtt_topic: str = ""
    direction: str = "bidir"          # tx | rx | bidir | to_gateway | from_gateway
    qos: int = 0
    retained: bool = False
    reliable: bool = False
    name: str | None = None
    fields: list[FieldSpec] = field(default_factory=list)
    transform: TopicTransform = field(default_factory=TopicTransform)

    def role_direction(self, role: str) -> str:
        """Resolve semantic direction ('to_gateway'/'from_node') to local
        flow direction ('tx' / 'rx' / 'bidir') for the given role.
        """
        d = (self.direction or "bidir").lower()
        if d in ("tx", "rx", "bidir"):
            return d
        if d == "to_gateway":
            return "tx" if role == "pi_node" else "rx"
        if d == "from_gateway":
            return "rx" if role == "pi_node" else "tx"
        return "bidir"


@dataclass
class BatteryRelay:
    """Zwei MQTT-Quell-Topics; jede Änderung wird an target gepublisht."""
    enabled: bool = False
    sources: list[str] = field(default_factory=list)
    target: str = "battery/cmd"
    payload_template: str = "{value}"

@dataclass
class SensorSpec:
    kind: str                    # bmp280 / aht20
    name: str
    poll_interval_s: float = 30.0
    topic_id: int = 0
    mqtt_topic: str | None = None
    i2c_bus: int = 1
    i2c_address: int = 0x77
    ack_req: bool = False


@dataclass
class HotspotConfig:
    enabled: bool = False        # nur informativ für Pi-Setup
    ssid: str = "lora-bridge"
    passphrase: str = "changeme12345"
    channel: int = 6
    ip_cidr: str = "192.168.50.1/24"


@dataclass
class ProbeConfig:
    tx_test: bool = False
    tx_test_payload: str = "PROBE"


@dataclass
class MqttSubscription:
    name: str = ""
    source_topic: str = ""
    # json_query accepts either dot-notation or a full jq expression.
    json_query: str | None = None
    extract: dict[str, str] = field(default_factory=dict)
    target_topic_id: int | None = None
    target_mqtt_topic: str | None = None
    payload_template: str | None = None
    # QoS the subscription is registered with on the local MQTT broker.
    subscribe_qos: int = 0
    # Publish QoS/retain if the subscription forwards to another MQTT topic.
    qos: int = 0
    retained: bool = False
    # Optional example payload (used for documentation / mock feeders).
    sample: Any = None


@dataclass
class OutputInput:
    """Named input to an mqtt_output expression.

    Extracts a value (and optional timestamp) from a source subscription
    using dot-notation or a jq expression.  `max_age` in seconds; if the
    extracted timestamp is older than that, the input is flagged `stale`.
    """
    subscription: str
    value: str = "."
    timestamp: str | None = None      # dot/jq expr; or "$received_at"
    max_age: float | None = None


@dataclass
class TimezoneValidation:
    max_offset: float = 3600.0
    on_error: str = "latch"           # latch | drop


@dataclass
class OutputValidation:
    powermeter_error_value: Any = None
    timezone: TimezoneValidation | None = None


@dataclass
class MqttOutput:
    """Derived output: recomputes a value whenever one of the trigger
    subscriptions receives a new message, then publishes to `target_topic`.
    """
    name: str
    trigger: list[str] = field(default_factory=list)
    target_topic: str = ""
    inputs: dict[str, OutputInput] = field(default_factory=dict)
    validation: OutputValidation = field(default_factory=OutputValidation)
    expression: str = "."
    qos: int = 0
    retained: bool = False


@dataclass
class Config:
    role: str = "pi_node"        # pi_node / ha_gateway
    log_level: str = "info"
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    lora: LoraConfig = field(default_factory=LoraConfig)
    ack: AckConfig = field(default_factory=AckConfig)
    topics: list[TopicMap] = field(default_factory=list)
    topics_file: str | None = None
    mqtt_subscriptions: list[MqttSubscription] = field(default_factory=list)
    mqtt_outputs: list[MqttOutput] = field(default_factory=list)
    battery_relay: BatteryRelay = field(default_factory=BatteryRelay)
    sensors: list[SensorSpec] = field(default_factory=list)
    hotspot: HotspotConfig = field(default_factory=HotspotConfig)
    probe: ProbeConfig = field(default_factory=ProbeConfig)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _apply(dc: Any, data: dict) -> Any:
    """Recursively apply a dict onto a dataclass object (best-effort)."""
    for key, value in data.items():
        if not hasattr(dc, key):
            continue
        current = getattr(dc, key)
        if hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
            _apply(current, value)
        else:
            setattr(dc, key, value)
    return dc


# --------------------------------------------------------------------------
# Load
# --------------------------------------------------------------------------
def load(path: str | os.PathLike | None = None) -> Config:
    """Loads config: YAML from `path` OR environment variable `LORA_BRIDGE_CONFIG`
    OR HA options `/data/options.json`.
    """
    cfg = Config()

    raw: dict = {}
    if path and Path(path).is_file():
        raw = yaml.safe_load(Path(path).read_text()) or {}
    elif os.environ.get("LORA_BRIDGE_CONFIG"):
        raw = yaml.safe_load(Path(os.environ["LORA_BRIDGE_CONFIG"]).read_text()) or {}
    elif Path("/data/options.json").is_file():
        raw = json.loads(Path("/data/options.json").read_text())

    # If loading an HA add-on manifest directly, unwrap the 'options' section
    if "options" in raw and isinstance(raw["options"], dict):
        raw = raw["options"]

    # Sync word may arrive as hex string (e.g. from HA UI)
    lora = raw.get("lora") or {}
    if isinstance(lora.get("sync_word"), str):
        try:
            lora["sync_word"] = int(lora["sync_word"], 0)
        except ValueError:
            log.warning("Invalid sync_word %r – keeping default", lora["sync_word"])
            lora.pop("sync_word", None)

    _apply(cfg, raw)

    # Load topics: from raw options or shared topics.yaml file
    cfg.topics = _load_topics(raw, path)
    cfg.sensors = [SensorSpec(**s) for s in raw.get("sensors", [])]
    cfg.mqtt_subscriptions = _parse_subscriptions(raw.get("mqtt_subscriptions", []))
    cfg.mqtt_outputs = _parse_outputs(raw.get("mqtt_outputs", []))

    # ---- Secondary secret overlays (override YAML defaults) ----
    _apply_secrets_file(cfg)
    _apply_env_overrides(cfg)
    return cfg


# --------------------------------------------------------------------------
# Topic parsing
# --------------------------------------------------------------------------
def _load_topics(raw: dict, config_path: str | os.PathLike | None) -> list[TopicMap]:
    """Loads topics either from raw config dict, or from a shared topics.yaml file."""
    # 1. If explicit topics are present in raw config, use them
    if raw.get("topics"):
        return _parse_topics(raw["topics"])

    # 2. Look for shared topics.yaml in candidate locations
    explicit_file = raw.get("topics_file") or os.environ.get("LORA_BRIDGE_TOPICS")
    candidate_paths: list[Path] = []

    if explicit_file:
        candidate_paths.append(Path(explicit_file))
        if config_path:
            candidate_paths.append(Path(config_path).parent / explicit_file)

    if config_path:
        candidate_paths.append(Path(config_path).parent / "topics.yaml")
        candidate_paths.append(Path(config_path).parent.parent / "topics.yaml")

    candidate_paths.extend([
        Path("/config/topics.yaml"),          # HA addon_config path
        Path("/app/topics.yaml"),             # Docker container path
        Path("/etc/lora-bridge/topics.yaml"), # Pi installed path
        Path("topics.yaml"),                  # Current directory / repo root
        Path("PiNode/topics.yaml"),
    ])

    for candidate in candidate_paths:
        if candidate.is_file():
            try:
                data = yaml.safe_load(candidate.read_text()) or {}
                if isinstance(data, dict) and "topics" in data:
                    return _parse_topics(data["topics"])
                if isinstance(data, list):
                    return _parse_topics(data)
            except Exception as exc:
                raise RuntimeError(
                    f"Error loading shared topics file '{candidate}': {exc}"
                ) from exc

    return []


def _parse_topics(raw_topics: list[dict] | None) -> list[TopicMap]:
    """Parse raw topic dictionary list into TopicMap and FieldSpec dataclasses.

    Accepts both the legacy flat schema and the new nested schema
    (`mqtt: {topic, qos, retained}`, `lora: {reliable}`, `transform`).
    """
    if not raw_topics:
        return []
    result: list[TopicMap] = []
    for t in raw_topics:
        if not isinstance(t, dict) or "id" not in t:
            log.warning("Skipping topic entry without id: %r", t)
            continue

        mqtt_block = t.get("mqtt") or {}
        lora_block = t.get("lora") or {}

        mqtt_topic = t.get("mqtt_topic") or mqtt_block.get("topic") or ""
        qos = int(mqtt_block.get("qos", t.get("qos", 0)))
        retained = bool(mqtt_block.get("retained", t.get("retained", False)))
        reliable = bool(lora_block.get("reliable", t.get("reliable", False)))

        # Parse fields
        raw_fields = t.get("fields", []) or []
        field_specs: list[FieldSpec] = []
        for f in raw_fields:
            if isinstance(f, dict):
                field_specs.append(FieldSpec(
                    name=str(f.get("name", "")),
                    type=str(f.get("type", "float")),
                ))
            elif isinstance(f, str):
                field_specs.append(FieldSpec(name=f, type="float"))

        # Parse transform section
        transform_raw = t.get("transform") or {}
        transform = TopicTransform(
            mqtt2lora=dict(transform_raw.get("mqtt2lora") or {}),
            lora2mqtt=dict(transform_raw.get("lora2mqtt") or {}),
        )

        result.append(TopicMap(
            id=int(t["id"]),
            mqtt_topic=str(mqtt_topic),
            direction=str(t.get("direction", "bidir")),
            qos=qos,
            retained=retained,
            reliable=reliable,
            name=t.get("name"),
            fields=field_specs,
            transform=transform,
        ))
    return result


# --------------------------------------------------------------------------
# Subscription & Output parsing
# --------------------------------------------------------------------------
def _parse_subscriptions(raw_subs: list[dict] | None) -> list[MqttSubscription]:
    if not raw_subs:
        return []
    valid_keys = set(MqttSubscription.__dataclass_fields__.keys())
    result: list[MqttSubscription] = []
    for s in raw_subs:
        if not isinstance(s, dict):
            continue
        clean = {k: v for k, v in s.items() if k in valid_keys}
        # Historic alias: "qos" alone was the subscribe QoS.  Prefer explicit
        # subscribe_qos when present, otherwise fall back to qos.
        if "subscribe_qos" not in clean and "qos" in clean:
            clean["subscribe_qos"] = int(clean["qos"])
        result.append(MqttSubscription(**clean))
    return result


def _parse_outputs(raw_outs: list[dict] | None) -> list[MqttOutput]:
    if not raw_outs:
        return []
    result: list[MqttOutput] = []
    for o in raw_outs:
        if not isinstance(o, dict) or not o.get("name"):
            continue

        inputs: dict[str, OutputInput] = {}
        for key, spec in (o.get("inputs") or {}).items():
            if not isinstance(spec, dict):
                continue
            inputs[key] = OutputInput(
                subscription=str(spec.get("subscription", "")),
                value=str(spec.get("value", ".")),
                timestamp=spec.get("timestamp"),
                max_age=spec.get("max_age"),
            )

        val_raw = o.get("validation") or {}
        tz_raw = val_raw.get("timezone")
        timezone = None
        if isinstance(tz_raw, dict):
            timezone = TimezoneValidation(
                max_offset=float(tz_raw.get("max_offset", 3600.0)),
                on_error=str(tz_raw.get("on_error", "latch")),
            )
        validation = OutputValidation(
            powermeter_error_value=val_raw.get("powermeter_error_value"),
            timezone=timezone,
        )

        result.append(MqttOutput(
            name=str(o["name"]),
            trigger=list(o.get("trigger") or []),
            target_topic=str(o.get("target_topic", "")),
            inputs=inputs,
            validation=validation,
            expression=str(o.get("expression", ".")),
            qos=int(o.get("qos", 0)),
            retained=bool(o.get("retained", False)),
        ))
    return result


# --------------------------------------------------------------------------
# Secret-Overlays: getrennte Datei + Env-Vars, damit MQTT-Login NICHT im Repo landet
# --------------------------------------------------------------------------
_SECRETS_PATHS = (
    # for PiNode-Installed
    "/etc/lora-bridge/secrets.yaml",
    "/etc/lora-bridge/secrets.yml",
    # for PiNode-Debug
    "PiNode/secrets.yaml",
    # for HA-App
    "/data/secrets.yaml",
)


def _apply_secrets_file(cfg: Config) -> None:
    candidates = [os.environ.get("LORA_BRIDGE_SECRETS", "")] + list(_SECRETS_PATHS)
    for path in candidates:
        if path and Path(path).is_file():
            try:
                data = yaml.safe_load(Path(path).read_text()) or {}
                _apply(cfg, data)
                if "topics" in data:
                    cfg.topics = _parse_topics(data["topics"])
                if "sensors" in data:
                    cfg.sensors = [SensorSpec(**s) for s in data["sensors"]]
                if "mqtt_subscriptions" in data:
                    cfg.mqtt_subscriptions = _parse_subscriptions(data["mqtt_subscriptions"])
                if "mqtt_outputs" in data:
                    cfg.mqtt_outputs = _parse_outputs(data["mqtt_outputs"])
            except Exception as exc:
                raise RuntimeError(f"Error loading secrets file {path}: {exc}") from exc
            break


_ENV_MAP = {
    "LORA_BRIDGE_MQTT_HOST":       ("mqtt", "host", str),
    "LORA_BRIDGE_MQTT_PORT":       ("mqtt", "port", int),
    "LORA_BRIDGE_MQTT_USER":       ("mqtt", "username", str),
    "LORA_BRIDGE_MQTT_PASS":       ("mqtt", "password", str),
    "LORA_BRIDGE_MQTT_TLS":        ("mqtt", "tls", lambda v: v.lower() in ("1", "true", "yes")),
    "LORA_BRIDGE_MQTT_CLIENT_ID":  ("mqtt", "client_id", str),
    "LORA_BRIDGE_LOG_LEVEL":       (None, "log_level", str),
    "LORA_BRIDGE_ROLE":            (None, "role", str),
}


def _apply_env_overrides(cfg: Config) -> None:
    for env_var, (section, key, caster) in _ENV_MAP.items():
        value = os.environ.get(env_var)
        if value is None or value == "":
            continue
        try:
            casted = caster(value)
        except Exception:
            continue
        target = getattr(cfg, section) if section else cfg
        setattr(target, key, casted)
