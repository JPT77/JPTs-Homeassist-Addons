"""HA-Addon Entry-Point.

Liest /data/options.json (via config_loader), erzwingt Rolle=ha_gateway,
mappt HA-Service-Env-Vars auf MQTT-Config, ruft den gemeinsamen main() auf.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, "/app")

from lora.config_loader import load  # noqa: E402
from lora.hw_probe import probe  # noqa: E402
from lora.logger import configure  # noqa: E402
from lora.role_ha_gateway import run  # noqa: E402


def main() -> int:
    cfg = load()  # nimmt /data/options.json automatisch
    cfg.role = "ha_gateway"

    if os.environ.get("LORA_BRIDGE_MQTT_HOST"):
        cfg.mqtt.host = os.environ["LORA_BRIDGE_MQTT_HOST"]
    if os.environ.get("LORA_BRIDGE_MQTT_PORT"):
        cfg.mqtt.port = int(os.environ["LORA_BRIDGE_MQTT_PORT"])
    if os.environ.get("LORA_BRIDGE_MQTT_USER"):
        cfg.mqtt.username = os.environ["LORA_BRIDGE_MQTT_USER"]
    if os.environ.get("LORA_BRIDGE_MQTT_PASS"):
        cfg.mqtt.password = os.environ["LORA_BRIDGE_MQTT_PASS"]

    log = configure(cfg.log_level)  # type: ignore[arg-type]
    log.info("HA-Addon lora_mqtt_gateway startet — log_level=%s", cfg.log_level)

    try:
        probe(cfg.lora, cfg.probe)
    except Exception as exc:
        log.error("Hardware-Probe fehlgeschlagen: %s", exc)
        return 2
    return run(cfg)


if __name__ == "__main__":
    sys.exit(main())
