"""Mock pi_node role.

Runs the pi_node application logic **without** the LoRa hardware layer:

* Uses an in-memory MQTT bridge (:class:`InMemoryMqttBridge`) – no
  Mosquitto required.
* Runs the standard :class:`MqttForwarder` (MQTT -> LoRa is stubbed so
  messages are just logged instead of transmitted).
* Runs the new :class:`MqttOutputEngine` (``mqtt_outputs`` section).
* Feeds mock MQTT messages taken from the ``sample`` field of each
  configured subscription on a fixed interval, so downstream logic is
  exercised end-to-end without any real broker or sensors.

Entry point: ``run(cfg)`` – same signature as :func:`Lora.role_pi_node.run`.
"""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
from typing import Any

from .config_loader import Config, MqttSubscription
from .mock_mqtt import InMemoryMqttBridge
from .mqtt_forwarder import MqttForwarder
from .mqtt_output_engine import MqttOutputEngine
from .topic_router import TopicRouter

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Stub LoRa bridge (satisfies MqttForwarder's `bridge` dependency)
# --------------------------------------------------------------------------
class StubLoraBridge:
    """Records all outgoing LoRa transmissions instead of sending them."""

    def __init__(self, cfg: Config | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.router = TopicRouter(cfg.topics, role=cfg.role) if cfg is not None else None

    def send_mqtt_over_lora(self, topic_id: int, payload: Any, reliable: bool) -> None:
        entry = {"kind": "encoded", "topic_id": topic_id, "payload": payload, "reliable": reliable}
        self.sent.append(entry)
        log.info("[stub-lora] encoded topic_id=%d reliable=%s payload=%r",
                 topic_id, reliable, payload)

    def send_raw_lora(self, topic_id: int, payload: bytes, reliable: bool) -> None:
        entry = {"kind": "raw", "topic_id": topic_id, "payload": payload, "reliable": reliable}
        self.sent.append(entry)
        log.info("[stub-lora] raw topic_id=%d reliable=%s bytes=%d",
                 topic_id, reliable, len(payload))


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def build(cfg: Config, feed_interval_s: float = 5.0) -> "MockPiNode":
    """Build a MockPiNode instance without starting the feeder loop."""
    return MockPiNode(cfg, feed_interval_s=feed_interval_s)


def run(cfg: Config, feed_interval_s: float = 5.0) -> int:
    """Run the mock pi_node until SIGINT/SIGTERM."""
    node = build(cfg, feed_interval_s=feed_interval_s)
    node.start()

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    log.info("mock pi_node running. Ctrl-C to exit.")
    try:
        while not stop.is_set():
            time.sleep(0.5)
    finally:
        log.info("Stopping mock pi_node...")
        node.stop()
    return 0


# --------------------------------------------------------------------------
# Mock node
# --------------------------------------------------------------------------
class MockPiNode:
    """Full pi_node stack with LoRa replaced by a stub and MQTT by an
    in-memory bridge.

    Public attributes useful in tests:

    * ``mqtt`` – the in-memory MQTT bridge (inspect ``mqtt.published()``)
    * ``bridge`` – the stub LoRa bridge (inspect ``bridge.sent``)
    * ``feed_once()`` – manually inject every sample subscription payload
      exactly once.
    """

    def __init__(self, cfg: Config, feed_interval_s: float = 5.0):
        self.cfg = cfg
        self.feed_interval_s = feed_interval_s
        self.mqtt = InMemoryMqttBridge()
        self.bridge = StubLoraBridge(cfg)
        self.forwarder = MqttForwarder(cfg.mqtt_subscriptions, self.bridge, self.mqtt)
        self.outputs = MqttOutputEngine(cfg.mqtt_outputs, cfg.mqtt_subscriptions, self.mqtt)

        self._stop = threading.Event()
        self._feeder_thread: threading.Thread | None = None
        self._samples: list[tuple[MqttSubscription, bytes]] = []

    # ------------------------------------------------------------ helpers
    def start(self) -> None:
        self.mqtt.connect()
        self.mqtt.set_on_message(self._on_message)

        self.forwarder.start()
        self.outputs.start()

        self._prepare_samples()
        if self._samples and self.feed_interval_s > 0:
            self._feeder_thread = threading.Thread(
                target=self._feeder_loop, name="mock-feeder", daemon=True,
            )
            self._feeder_thread.start()

        log.info(
            "MockPiNode started: %d subscriptions, %d outputs, %d sample feeders",
            len(self.cfg.mqtt_subscriptions), len(self.cfg.mqtt_outputs), len(self._samples),
        )

    def stop(self) -> None:
        self._stop.set()
        if self._feeder_thread:
            self._feeder_thread.join(timeout=2)
        self.mqtt.stop()

    def feed_once(self) -> int:
        """Inject every sample payload exactly once. Returns count."""
        count = 0
        for sub, payload in self._samples:
            if self.mqtt.inject(sub.source_topic, payload):
                count += 1
        return count

    # ---------------------------------------------------------------- MQTT
    def _on_message(self, topic: str, payload: bytes) -> None:
        try:
            handled = self.forwarder.handle_message(topic, payload)
        except Exception:
            log.exception("forwarder failed for %s", topic)
            handled = False
        try:
            handled |= self.outputs.handle_message(topic, payload)
        except Exception:
            log.exception("output engine failed for %s", topic)

        if not handled:
            log.debug("mock: message on %s ignored (no handler)", topic)

    # ------------------------------------------------------------ feeder
    def _prepare_samples(self) -> None:
        for sub in self.cfg.mqtt_subscriptions:
            if not sub.source_topic or sub.sample is None:
                continue
            if isinstance(sub.sample, (dict, list)):
                payload = json.dumps(sub.sample).encode("utf-8")
            elif isinstance(sub.sample, bytes):
                payload = sub.sample
            else:
                payload = str(sub.sample).encode("utf-8")
            self._samples.append((sub, payload))

    def _feeder_loop(self) -> None:
        # Initial fast burst so downstream state is populated immediately.
        for sub, payload in self._samples:
            if self._stop.is_set():
                return
            self.mqtt.inject(sub.source_topic, payload)
        while not self._stop.wait(self.feed_interval_s):
            for sub, payload in self._samples:
                if self._stop.is_set():
                    return
                self.mqtt.inject(sub.source_topic, payload)
