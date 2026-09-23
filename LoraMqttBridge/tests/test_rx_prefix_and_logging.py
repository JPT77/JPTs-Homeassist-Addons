"""Unit tests for rx_topic_prefix and decoded MQTT RX delivery."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# Stub paho / paho.mqtt if not installed in current environment
if "paho" not in sys.modules:
    sys.modules["paho"] = MagicMock()
    sys.modules["paho.mqtt"] = MagicMock()
    sys.modules["paho.mqtt.client"] = MagicMock()

from Lora.bridge import Bridge
from Lora.config_loader import Config, FieldSpec, LoraConfig, MqttConfig, TopicMap, load
from Lora.discovery import announce
from Lora.protocol import Frame, FrameType


class RxPrefixAndLoggingTests(unittest.TestCase):
    def test_config_rx_topic_prefix_default(self) -> None:
        cfg = Config()
        self.assertEqual(cfg.rx_topic_prefix, "")

    def test_config_rx_topic_prefix_env(self) -> None:
        os.environ["LORA_BRIDGE_RX_TOPIC_PREFIX"] = "TEST/"
        try:
            cfg = load()
            self.assertEqual(cfg.rx_topic_prefix, "TEST/")
        finally:
            os.environ.pop("LORA_BRIDGE_RX_TOPIC_PREFIX", None)

    def test_deliver_mqtt_with_rx_prefix_and_logging(self) -> None:
        topic_map = TopicMap(
            id=11,
            mqtt_topic="tele/HichiIR/SENSOR",
            direction="from_node",  # will be rx on ha_gateway
            qos=1,
            retained=False,
            fields=[FieldSpec(name="power", type="int16")],
        )
        cfg = Config(
            role="ha_gateway",
            rx_topic_prefix="TEST/",
            topics=[topic_map],
        )

        mock_radio = MagicMock()
        mock_mqtt = MagicMock()
        bridge = Bridge(cfg, mock_radio, mock_mqtt)

        # Build raw int16 binary payload for power = 42 (2 bytes: 0x2A 0x00)
        raw_payload = (42).to_bytes(2, byteorder="little", signed=True)
        frame = Frame(
            version=1,
            ftype=FrameType.MQTT,
            ack_req=False,
            seq=5,
            topic_id=11,
            payload=raw_payload,
        )

        with self.assertLogs("Lora.bridge", level="INFO") as log_cm:
            bridge._deliver_mqtt(frame)

        # Check MQTT publish was called with TEST/ prefix
        mock_mqtt.publish.assert_called_once()
        call_args = mock_mqtt.publish.call_args
        pub_topic = call_args[0][0]
        pub_payload = call_args[0][1]

        self.assertEqual(pub_topic, "TEST/tele/HichiIR/SENSOR")
        payload_dict = json.loads(pub_payload.decode("utf-8"))
        self.assertEqual(payload_dict, {"power": 42})

        # Check that log contains info with topic and payload
        log_output = "\n".join(log_cm.output)
        self.assertIn("RX LoRa -> MQTT [TEST/tele/HichiIR/SENSOR]", log_output)
        self.assertIn('"power": 42', log_output)

    def test_discovery_with_rx_prefix(self) -> None:
        topic_map = TopicMap(
            id=11,
            mqtt_topic="tele/HichiIR/SENSOR",
            direction="to_gateway",
            qos=1,
            retained=False,
            fields=[FieldSpec(name="power", type="int16")],
        )
        cfg = Config(
            role="ha_gateway",
            rx_topic_prefix="TEST/",
            topics=[topic_map],
            mqtt=MqttConfig(client_id="test_gateway"),
        )
        mock_mqtt = MagicMock()
        announce(cfg, mock_mqtt)

        # Verify discovery published state_topic with TEST/ prefix
        mock_mqtt.publish.assert_called()
        configs = [json.loads(call[0][1]) for call in mock_mqtt.publish.call_args_list]
    def test_output_engine_with_rx_prefix(self) -> None:
        from Lora.config_loader import MqttOutput, MqttSubscription, OutputInput
        from Lora.mqtt_output_engine import MqttOutputEngine

        sub1 = MqttSubscription(name="powermeter_power", source_topic="tele/HichiIR/SENSOR")
        sub2 = MqttSubscription(name="battery_power", source_topic="homeassistant/sensor/quick/state")

        output = MqttOutput(
            name="battery_power_control",
            trigger=["powermeter_power", "battery_power"],
            target_topic="homeassistant/number/power_ctrl/set",
            inputs={
                "powermeter": OutputInput(subscription="powermeter_power", value=".EMH.Power", timestamp="$received_at"),
                "battery": OutputInput(subscription="battery_power", value=".bat_p", timestamp="$received_at"),
            },
            expression="(.powermeter.value + .battery.value)",
        )

        cfg = Config(
            rx_topic_prefix="TEST/",
            mqtt_subscriptions=[sub1, sub2],
            mqtt_outputs=[output],
        )

        mock_mqtt = MagicMock()
        mock_radio = MagicMock()
        bridge = Bridge(cfg, mock_radio, mock_mqtt)
        bridge.start()

        with unittest.mock.patch("Lora.mqtt_output_engine.run_jq", return_value=150):
            with self.assertLogs("Lora.mqtt_output_engine", level="INFO") as log_cm:
                # Inject both inputs
                bridge._on_mqtt("tele/HichiIR/SENSOR", json.dumps({"EMH": {"Power": 120}}).encode())
                bridge._on_mqtt("homeassistant/sensor/quick/state", json.dumps({"bat_p": 30}).encode())

        # Check MQTT publish on output target topic was prefixed with TEST/
        mock_mqtt.publish.assert_called()
        calls = [c[0] for c in mock_mqtt.publish.call_args_list if c[0][0].startswith("TEST/homeassistant/number/power_ctrl/set")]
        self.assertGreater(len(calls), 0)
        last_call = calls[-1]
        self.assertEqual(last_call[0], "TEST/homeassistant/number/power_ctrl/set")
        self.assertEqual(last_call[1], b"150")

        # Check log output
        log_output = "\n".join(log_cm.output)
        self.assertIn("mqtt_output 'battery_power_control' -> TEST/homeassistant/number/power_ctrl/set", log_output)


if __name__ == "__main__":
    unittest.main()
