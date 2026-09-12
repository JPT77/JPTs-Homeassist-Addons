"""CLI entry point for the mock pi_node variant.

Usage:
    python3 -m Lora.entry_mock --config PiNode/config.yaml

Runs the pi_node stack without any real LoRa hardware or MQTT broker.
Sample payloads from the subscriptions defined in the config are fed
into the in-memory MQTT bridge at a fixed interval.
"""

from __future__ import annotations

import argparse
import sys

from .config_loader import load
from .logger import configure
from .role_pi_node_mock import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="Lora.entry_mock")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--feed-interval", type=float, default=5.0,
                        help="Seconds between sample injections (0 disables)")
    args = parser.parse_args(argv)

    cfg = load(args.config)
    cfg.role = "pi_node"
    log = configure(cfg.log_level)
    log.info("mock pi_node starting (log_level=%s)", cfg.log_level)
    return run(cfg, feed_interval_s=args.feed_interval)


if __name__ == "__main__":
    sys.exit(main())
