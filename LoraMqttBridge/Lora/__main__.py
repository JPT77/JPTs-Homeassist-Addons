"""CLI-Entry-Point:

  python3 -m lora_mqtt_bridge [--config /path/to/config.yaml]

Ohne --config wird `/data/options.json` (HA-Addon) oder Env-Var gelesen.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .config_loader import load
from .hw_probe import probe
from .logger import configure



def main(argv: list[str] | None = None) -> int:
    import logging
    log = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(prog="Lora")
    parser.add_argument("--config", default=None, help="Pfad zu config.yaml")
    parser.add_argument("--skip-probe", action="store_true",
                        help="Hardware-Probe überspringen (nur für Tests)")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)

    if args.version:
        print(__version__)
        return 0

    log.info(args)
    log.info(args.config)

    cfg = load(args.config)
    log = configure(cfg.log_level)  # type: ignore[arg-type]
    log.info("lora_mqtt_bridge v%s — role=%s log_level=%s",
             __version__, cfg.role, cfg.log_level)

    if not args.skip_probe:
        try:
            probe(cfg.lora, cfg.probe)
        except Exception as exc:
            log.error("Hardware-Probe fehlgeschlagen: %s", exc)
            return 2

    if cfg.role == "pi_node":
        from .role_pi_node import run
    elif cfg.role == "ha_gateway":
        from .role_ha_gateway import run
    else:
        log.error("Unbekannte Rolle: %s", cfg.role)
        return 3
    return run(cfg)


if __name__ == "__main__":
    sys.exit(main())
