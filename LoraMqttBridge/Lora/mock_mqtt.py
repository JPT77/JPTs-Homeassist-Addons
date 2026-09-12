"""In-memory MQTT bridge for tests and the mock pi_node variant.

Implements the same public interface as :class:`Lora.mqtt_client.MqttBridge`
(``connect``, ``stop``, ``subscribe``, ``publish``, ``set_on_message``)
but keeps everything inside the process – no network, no broker required.

Supports very simple MQTT wildcards (``+`` for one level, ``#`` for the
rest) so the mock feeder can hand a message to whichever subscription is
configured for its source topic.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Callable

log = logging.getLogger(__name__)


def _wildcard_to_regex(pattern: str) -> re.Pattern:
    parts = []
    for token in pattern.split("/"):
        if token == "#":
            parts.append(".*")
            break
        if token == "+":
            parts.append("[^/]+")
        else:
            parts.append(re.escape(token))
    return re.compile("^" + "/".join(parts) + "$")


class InMemoryMqttBridge:
    """Minimal in-memory pub/sub replacement for MqttBridge."""

    def __init__(self, on_message: Callable[[str, bytes], None] | None = None):
        self._on_message = on_message
        self._subscriptions: list[tuple[str, re.Pattern, int]] = []
        self._published: list[tuple[str, bytes, int, bool]] = []
        self._lock = threading.Lock()
        self._connected = False

    # -- lifecycle --------------------------------------------------------
    def connect(self) -> None:
        self._connected = True
        log.info("InMemoryMqttBridge: connect (mock)")

    def stop(self) -> None:
        self._connected = False

    # -- API --------------------------------------------------------------
    def subscribe(self, topic: str, qos: int = 0) -> None:
        with self._lock:
            self._subscriptions.append((topic, _wildcard_to_regex(topic), qos))
        log.debug("mock subscribe %s (qos=%d)", topic, qos)

    def publish(self, topic: str, payload: bytes | str, qos: int = 0, retain: bool = False) -> None:
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        with self._lock:
            self._published.append((topic, payload, qos, retain))
        log.debug("mock publish %s = %s", topic, payload[:200])
        # Route to matching subscriptions (echo behaviour disabled – mimic
        # a real broker where our own publish does not trigger our own
        # subscribe unless someone else subscribed to it).

    def set_on_message(self, cb: Callable[[str, bytes], None]) -> None:
        self._on_message = cb

    # -- test helpers -----------------------------------------------------
    def inject(self, topic: str, payload: bytes | str) -> bool:
        """Deliver a message from the outside world to matching subscribers."""
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        with self._lock:
            matches = [t for t, pat, _q in self._subscriptions if pat.match(topic)]
        if not matches:
            return False
        if self._on_message:
            try:
                self._on_message(topic, payload)
            except Exception:
                log.exception("mock on_message failed for %s", topic)
        return True

    def published(self) -> list[tuple[str, bytes, int, bool]]:
        with self._lock:
            return list(self._published)

    def clear_published(self) -> None:
        with self._lock:
            self._published.clear()
