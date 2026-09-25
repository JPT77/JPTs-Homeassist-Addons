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

from pathlib import Path
from unittest.mock import MagicMock

# Stub paho / paho.mqtt if not installed in current environment
if "paho" not in sys.modules:
    sys.modules["paho"] = MagicMock()
    sys.modules["paho.mqtt"] = MagicMock()
    sys.modules["paho.mqtt.client"] = MagicMock()

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
        self.assertIn(self.cfg.log_level, ("debug", "info"))

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
        self.assertEqual(t20.role_direction("pi_node"), "tx")
        self.assertEqual(t20.role_direction("ha_gateway"), "rx")
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

    def test_config_source_parsed(self) -> None:
        out = self.cfg.mqtt_outputs[0]
        self.assertIsNotNone(out.config_source)
        self.assertEqual(
            out.config_source.topic,
            "homeassistant/number/MSA-280425440006/power_ctrl/config",
        )
        self.assertEqual(out.config_source.extract["min"], ".min")
        self.assertEqual(out.config_source.extract["max"], ".max")


def _eval_mock_expression(expr: str, inputs: dict) -> Any:
    pm = inputs.get("powermeter", {})
    bat = inputs.get("battery", {})
    if pm.get("error") or pm.get("stale"):
        return 0
    pv = pm.get("value") or 0
    bv = bat.get("value") or 0
    s = pv + bv
    cfg = inputs.get("config") or {}
    lo = cfg.get("min", -800)
    hi = cfg.get("max", 800)
    return max(lo, min(hi, s))


# HA-Discovery style config payload for the battery number entity.
CONFIG_PAYLOAD = {
    "name": None,
    "command_topic": "homeassistant/number/MSA-280425440006/power_ctrl/set",
    "device_class": "power",
    "unit_of_measurement": "W",
    "min": -800,
    "max": 1000,
    "step": 0.1,
    "unique_id": "MSA-280425440006",
}
CONFIG_TOPIC = "homeassistant/number/MSA-280425440006/power_ctrl/config"


class MockPiNodeIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        os.chdir(REPO_ROOT)
        self.cfg = load("PiNode/config.yaml")
        # Replace user's jq expression with the config-driven variant
        # so we can exercise the output engine end-to-end without jq.
        self.cfg.mqtt_outputs[0].expression = VALID_EXPRESSION
        # Add a sample to powermeter_status so LoRa forwarding fires
        by_name = {s.name: s for s in self.cfg.mqtt_subscriptions}
        by_name["powermeter_status"].sample = {
            "Time": "2026-09-10T06:37:16",
            "Uptime": 12345,
            "MqttCount": 2,
            "Wifi": {"Signal": 65, "RSSI": 70, "Downtime": 0, "LinkCount": 1},
        }

    def _inject_config(self, node) -> None:
        """Push the retained HA-discovery config message into the mock broker."""
        node.mqtt.inject(CONFIG_TOPIC, json.dumps(CONFIG_PAYLOAD))

    def test_feed_once_publishes_output_and_lora(self) -> None:
        node = MockPiNode(self.cfg, feed_interval_s=0)
        node.start()
        try:
            with unittest.mock.patch("Lora.mqtt_output_engine.run_jq", side_effect=_eval_mock_expression):
                self._inject_config(node)
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
            with unittest.mock.patch("Lora.mqtt_output_engine.run_jq", side_effect=_eval_mock_expression):
                self._inject_config(node)
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

    def test_output_uses_dynamic_min_max_from_config_source(self) -> None:
        """Sum outside [config.min, config.max] must be clamped to those
        values from the retained HA-discovery message – NOT hardcoded."""
        node = MockPiNode(self.cfg, feed_interval_s=0)
        node.start()
        try:
            import time
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            with unittest.mock.patch("Lora.mqtt_output_engine.run_jq", side_effect=_eval_mock_expression):
                self._inject_config(node)

                # sum = 3000, must clamp to config.max = 1000
                node.mqtt.inject("tele/HichiIR/SENSOR",
                                 json.dumps({"Time": now_iso, "EMH": {"Power": 2000}}))
                node.mqtt.inject("homeassistant/sensor/MSA-280425440006/quick/state",
                                 json.dumps({"bat_p": 1000}))

                target = self.cfg.mqtt_outputs[0].target_topic
                hits = [p for p in node.mqtt.published() if p[0] == target]
                self.assertEqual(hits[-1][1].decode(), "1000")

                # sum = -1500, must clamp to config.min = -800
                node.mqtt.inject("tele/HichiIR/SENSOR",
                                 json.dumps({"Time": now_iso, "EMH": {"Power": -500}}))
                node.mqtt.inject("homeassistant/sensor/MSA-280425440006/quick/state",
                                 json.dumps({"bat_p": -1000}))
                hits = [p for p in node.mqtt.published() if p[0] == target]
                self.assertEqual(hits[-1][1].decode(), "-800")
        finally:
            node.stop()

    def test_output_skipped_without_config(self) -> None:
        """No publish before the retained HA-discovery config has arrived."""
        node = MockPiNode(self.cfg, feed_interval_s=0)
        node.start()
        try:
            import time
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            with unittest.mock.patch("Lora.mqtt_output_engine.run_jq", side_effect=_eval_mock_expression):
                # NOTE: no config injection yet
                node.mqtt.inject("tele/HichiIR/SENSOR",
                                 json.dumps({"Time": now_iso, "EMH": {"Power": 10}}))
                node.mqtt.inject("homeassistant/sensor/MSA-280425440006/quick/state",
                                 json.dumps({"bat_p": 20}))

            target = self.cfg.mqtt_outputs[0].target_topic
            hits = [p for p in node.mqtt.published() if p[0] == target]
            self.assertEqual(hits, [], "must not publish before config arrives")
        finally:
            node.stop()


if __name__ == "__main__":
    unittest.main()
