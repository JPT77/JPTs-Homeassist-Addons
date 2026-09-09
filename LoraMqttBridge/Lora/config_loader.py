"""Config-Loader für YAML-Datei (Pi) und HA-Add-on Options (HA-Gateway)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


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
class TopicMap:
    id: int
    mqtt_topic: str
    direction: str = "bidir"   # rx / tx / bidir
    qos: int = 0
    retained: bool = False
    fields: list[FieldSpec] = field(default_factory=list)


@dataclass
class BatteryRelay:
    """Zwei MQTT-Quell-Topics; jede Änderung wird an target gepublisht."""
    enabled: bool = False
    sources: list[str] = field(default_factory=list)
    target: str = "battery/cmd"
    payload_template: str = "{value}"


@dataclass
class SensorSpec:
    kind: str                    # bmp280 / aht20 / adc_mcp3008 / adc_ads1115
    name: str
    poll_interval_s: float = 30.0
    topic_id: int = 0
    mqtt_topic: str | None = None
    i2c_bus: int = 1
    i2c_address: int = 0x77
    channel: int = 0             # ADC-Kanal
    gain: float = 1.0            # ADS1115 gain
    vref: float = 3.3            # MCP3008 Referenz
    field: str = "value"         # z. B. temperature / pressure / humidity
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
    # json_query accepts either:
    #   - A simple dot-notation path:  "battery.soc" or "Wifi.Signal"
    #   - A full jq expression (single- or multi-line) that uses helpers from
    #     dataconvert.jq (u8, u16, u32, i8, i16, i32, be16, be32, duration_sec).
    #     When the query returns a flat int array [b0, b1, ...], it is sent as
    #     raw bytes over LoRa, bypassing PayloadCodec.
    json_query: str | None = None
    # extract: alternative to json_query – maps field names to dot-notation paths.
    # Result is encoded by PayloadCodec using the field types from topics.yaml.
    extract: dict[str, str] = field(default_factory=dict)
    target_topic_id: int | None = None          # Forwards over LoRa if set
    target_mqtt_topic: str | None = None        # Forwards to local MQTT topic if set
    payload_template: str | None = None         # Only used with target_mqtt_topic
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
    battery_relay: BatteryRelay = field(default_factory=BatteryRelay)
    sensors: list[SensorSpec] = field(default_factory=list)
    hotspot: HotspotConfig = field(default_factory=HotspotConfig)
    probe: ProbeConfig = field(default_factory=ProbeConfig)


def _apply(dc: Any, data: dict) -> Any:
    """Recursively apply a dict onto a dataclass object."""
    for key, value in data.items():
        if not hasattr(dc, key):
            continue
        current = getattr(dc, key)
        if hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
            _apply(current, value)
        else:
            setattr(dc, key, value)
    return dc


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
        lora["sync_word"] = int(lora["sync_word"], 0)

    _apply(cfg, raw)

    # Load topics: from raw options or shared topics.yaml file
    cfg.topics = _load_topics(raw, path)
    cfg.sensors = [SensorSpec(**s) for s in raw.get("sensors", [])]
    cfg.mqtt_subscriptions = _parse_subscriptions(raw.get("mqtt_subscriptions", []))

    # ---- Secondary secret overlays (override YAML defaults) ----
    _apply_secrets_file(cfg)
    _apply_env_overrides(cfg)
    return cfg


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
    """Parse raw topic dictionary list into TopicMap and FieldSpec dataclasses."""
    if not raw_topics:
        return []
    result: list[TopicMap] = []
    for t in raw_topics:
        raw_fields = t.get("fields", [])
        field_specs: list[FieldSpec] = []
        for f in raw_fields:
            if isinstance(f, dict):
                field_specs.append(
                    FieldSpec(
                        name=str(f.get("name", "")),
                        type=str(f.get("type", "float")),
                    )
                )
            elif isinstance(f, str):
                field_specs.append(FieldSpec(name=f, type="float"))
        item_data = {k: v for k, v in t.items() if k != "fields"}
        result.append(TopicMap(**item_data, fields=field_specs))
    return result


def _parse_subscriptions(raw_subs: list[dict] | None) -> list[MqttSubscription]:
    """Parse raw MQTT subscription list into MqttSubscription dataclass instances."""
    if not raw_subs:
        return []
    result: list[MqttSubscription] = []
    for s in raw_subs:
        if isinstance(s, dict):
            result.append(MqttSubscription(**s))
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
    """Applies overlay settings from the first existing secrets file."""
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
            except Exception as exc:
                raise RuntimeError(
                    f"Error loading secrets file {path}: {exc}"
                ) from exc
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
