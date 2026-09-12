"""End-to-end tests for the mock pi_node stack.

Run with:
    cd /app/repo/LoraMqttBridge
    python3 -m unittest tests.test_mock_pi_node
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from Lora.config_loader import load  # noqa: E402
from Lora.role_pi_node_mock import MockPiNode  # noqa: E402


VALID_EXPRESSION = """
if .powermeter.error or .powermeter.stale then 0
else (
  (.powermeter.value + .battery.value) as $s
  | if $s < -800 then -800 elif $s > 800 then 800 else $s end
)
end
"""


class ConfigLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        os.chdir(REPO_ROOT)
        self.cfg = load("PiNode/config.yaml")

    def test_role_and_log_level(self) -> None:
        self.assertEqual(self.cfg.role, "pi_node")
        self.assertEqual(self.cfg.log_level, "debug")

    def test_topics_new_schema(self) -> None:
        by_id = {t.id: t for t in self.cfg.topics}
        self.assertIn(10, by_id)
        self.assertIn(20, by_id)
        t10 = by_id[10]
        self.assertEqual(t10.mqtt_topic, "tele/HichiIR/STATE")
        self.assertEqual(t10.direction, "to_gateway")
        self.assertEqual(t10.qos, 1)
        self.assertTrue(t10.reliable)
        self.assertEqual(len(t10.fields), 7)
        # Role-aware direction: to_gateway → tx for pi_node, rx for ha_gateway
        self.assertEqual(t10.role_direction("pi_node"), "tx")
        self.assertEqual(t10.role_direction("ha_gateway"), "rx")

        t20 = by_id[20]
        self.assertEqual(t20.direction, "from_node")
        self.assertEqual(t20.role_direction("pi_node"), "rx")
        self.assertEqual(t20.role_direction("ha_gateway"), "tx")
        self.assertTrue(t20.retained)

    def test_transform_parsed(self) -> None:
        by_id = {t.id: t for t in self.cfg.topics}
        self.assertIn("timestamp", by_id[10].transform.mqtt2lora)
        self.assertIn("Time", by_id[10].transform.lora2mqtt)

    def test_subscriptions_with_sample(self) -> None:
        by_name = {s.name: s for s in self.cfg.mqtt_subscriptions}
        self.assertIn("powermeter_status", by_name)
        self.assertEqual(by_name["powermeter_status"].target_topic_id, 10)
        self.assertEqual(by_name["powermeter_status"].subscribe_qos, 1)
        self.assertIsNotNone(by_name["powermeter_power"].sample)
        self.assertIsNotNone(by_name["battery_power"].sample)

    def test_output_parsed(self) -> None:
        self.assertEqual(len(self.cfg.mqtt_outputs), 1)
        out = self.cfg.mqtt_outputs[0]
        self.assertEqual(out.name, "battery_power_control")
        self.assertEqual(set(out.trigger), {"powermeter_power", "battery_power"})
        self.assertIn("powermeter", out.inputs)
        self.assertEqual(out.inputs["powermeter"].value, ".EMH.Power")
        self.assertEqual(out.inputs["battery"].timestamp, "$received_at")
        self.assertEqual(out.validation.powermeter_error_value, 999999)
        self.assertIsNotNone(out.validation.timezone)
        self.assertEqual(out.validation.timezone.on_error, "latch")


class MockPiNodeIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        os.chdir(REPO_ROOT)
        self.cfg = load("PiNode/config.yaml")
        # Replace user's syntactically-broken jq expression with a valid one
        # so we can exercise the output engine end-to-end.
        self.cfg.mqtt_outputs[0].expression = VALID_EXPRESSION
        # Add a sample to powermeter_status so LoRa forwarding fires
        by_name = {s.name: s for s in self.cfg.mqtt_subscriptions}
        by_name["powermeter_status"].sample = {
            "Time": "2026-09-10T06:37:16",
            "Uptime": 12345,
            "MqttCount": 2,
            "Wifi": {"Signal": 65, "RSSI": 70, "Downtime": 0, "LinkCount": 1},
        }

    def test_feed_once_publishes_output_and_lora(self) -> None:
        node = MockPiNode(self.cfg, feed_interval_s=0)
        node.start()
        try:
            n = node.feed_once()
            self.assertGreaterEqual(n, 3)

            # LoRa stub received the powermeter_status forward
            forwards = [f for f in node.bridge.sent if f["topic_id"] == 10]
            self.assertEqual(len(forwards), 1)
            # reliability flag propagated from topic 10's lora.reliable = true
            self.assertTrue(forwards[0]["reliable"])

            # The output engine published something (may be 0 due to stale
            # sample timestamp, but the target topic must have been hit)
            target = self.cfg.mqtt_outputs[0].target_topic
            hits = [p for p in node.mqtt.published() if p[0] == target]
            self.assertGreater(len(hits), 0)
        finally:
            node.stop()

    def test_output_uses_recent_data(self) -> None:
        """With fresh timestamps the output should reflect the summed value."""
        node = MockPiNode(self.cfg, feed_interval_s=0)
        node.start()
        try:
            import time
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            node.mqtt.inject("tele/HichiIR/SENSOR",
                             json.dumps({"Time": now_iso, "EMH": {"Power": 100}}))
            node.mqtt.inject("homeassistant/sensor/MSA-280425440006/quick/state",
                             json.dumps({"bat_p": 50}))

            target = self.cfg.mqtt_outputs[0].target_topic
            hits = [p for p in node.mqtt.published() if p[0] == target]
            self.assertGreater(len(hits), 0)
            last = hits[-1][1].decode()
            self.assertEqual(last, "150")
        finally:
            node.stop()


if __name__ == "__main__":
    unittest.main()
