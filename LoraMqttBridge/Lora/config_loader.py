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
class TopicMap:
    id: int
    mqtt_topic: str
    direction: str = "bidir"   # rx / tx / bidir
    qos: int = 0
    retained: bool = False


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
class Config:
    role: str = "pi_node"        # pi_node / ha_gateway
    log_level: str = "info"
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    lora: LoraConfig = field(default_factory=LoraConfig)
    ack: AckConfig = field(default_factory=AckConfig)
    topics: list[TopicMap] = field(default_factory=list)
    battery_relay: BatteryRelay = field(default_factory=BatteryRelay)
    sensors: list[SensorSpec] = field(default_factory=list)
    hotspot: HotspotConfig = field(default_factory=HotspotConfig)
    probe: ProbeConfig = field(default_factory=ProbeConfig)


def _apply(dc: Any, data: dict) -> Any:
    """Rekursives Anwenden eines dicts auf ein Dataclass-Objekt."""
    for key, value in data.items():
        if not hasattr(dc, key):
            continue
        current = getattr(dc, key)
        if hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
            _apply(current, value)
        else:
            setattr(dc, key, value)
            print(f"settattr({dc}, {key}, {value})")
    return dc


def load(path: str | os.PathLike | None = None) -> Config:
    """Lädt Config: YAML aus `path` ODER Umgebungsvariable `LORA_BRIDGE_CONFIG`
    ODER HA-Options `/data/options.json`.
    """
    print(f"Load config from {path}")

    cfg = Config()

    raw: dict = {}
    if path and Path(path).is_file():
        raw = yaml.safe_load(Path(path).read_text()) or {}
    elif os.environ.get("LORA_BRIDGE_CONFIG"):
        raw = yaml.safe_load(Path(os.environ["LORA_BRIDGE_CONFIG"]).read_text()) or {}
    elif Path("/data/options.json").is_file():
        raw = json.loads(Path("/data/options.json").read_text())

    # Sync-Word darf als Hex-String kommen (aus HA-UI)
    lora = raw.get("lora") or {}
    if isinstance(lora.get("sync_word"), str):
        lora["sync_word"] = int(lora["sync_word"], 0)

    _apply(cfg, raw)

    # Listen manuell in ihre Dataclasses konvertieren
    cfg.topics = [TopicMap(**t) for t in raw.get("topics", [])]
    cfg.sensors = [SensorSpec(**s) for s in raw.get("sensors", [])]

    # ---- Sekundäre Secret-Quellen (überschreiben die YAML-Defaults) ----
    _apply_secrets_file(cfg)
    _apply_env_overrides(cfg)
    return cfg


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
    """Überlagert cfg mit Werten aus der ersten existierenden Secret-Datei.

    Format identisch zur Haupt-Config, meist nur ein Ausschnitt:

        mqtt:
          username: lora
          password: super-secret

    Pfad kann per Env `LORA_BRIDGE_SECRETS` überschrieben werden.
    """
    candidates = [os.environ.get("LORA_BRIDGE_SECRETS", "")] + list(_SECRETS_PATHS)
    for path in candidates:
        print(f"Checking {path}")
        if path and Path(path).is_file():
            try:
                print(f"Found secrets file {path}")
                data = yaml.safe_load(Path(path).read_text()) or {}
                _apply(cfg, data)
                if "topics" in data:
                    cfg.topics = [TopicMap(**t) for t in data["topics"]]
                if "sensors" in data:
                    cfg.sensors = [SensorSpec(**s) for s in data["sensors"]]
            except Exception as exc:
                raise RuntimeError(
                    f"Fehler beim Laden der Secret-Datei {path}: {exc}"
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
        print(f"Checking {section}.{key}")
        value = os.environ.get(env_var)
        if value is None or value == "":
            continue
        try:
            print(f"Settings {key}={value}")
            casted = caster(value)
        except Exception:
            continue
        target = getattr(cfg, section) if section else cfg
        setattr(target, key, casted)
