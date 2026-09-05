"""LoRa ↔ MQTT Bridge — gemeinsame Codebasis für Pi-Node und HA-Gateway."""

from pathlib import Path
import yaml


def load_version() -> str:
    path = Path(__file__).resolve().parent.parent / "config.yaml"

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return str(data["version"])


__version__ = load_version()
