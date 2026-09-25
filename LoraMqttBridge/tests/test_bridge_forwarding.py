"""Tests for the production `Bridge` class end-to-end.

Focus: verify that MQTT messages arriving on `tele/HichiIR/STATE`
(target_topic_id=10) and `tasmota/discovery/.../sensors` (target_topic_id=12)
actually reach the LoRa layer.  This reproduces the bug the user reported:
sensors 20/21/0 forwarded correctly, but 10/12 did not.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# Stub paho for import
if "paho" not in sys.modules:
    sys.modules["paho"] = MagicMock()
    sys.modules["paho.mqtt"] = MagicMock()
    sys.modules["paho.mqtt.client"] = MagicMock()

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from Lora.bridge import Bridge  # noqa: E402
from Lora.config_loader import load  # noqa: E402
from Lora.mock_mqtt import InMemoryMqttBridge  # noqa: E402


class StubRadio:
    """Minimal LoraRadio replacement: records every TX frame."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self._rx_q: queue.Queue = queue.Queue()

    def send(self, data: bytes) -> bool:
        self.sent.append(data)
        return True

    def get_rx(self, timeout: float = 0.2):
        try:
            return self._rx_q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        pass


def _parse_lora_frame(data: bytes) -> dict:
    """Decode header of a MQTT LoRa frame (see Lora/protocol.py)."""
    from Lora.protocol import Frame
    frame = Frame.decode(data)
    return {
        "version": frame.version,
        "ftype": frame.ftype,
        "seq": frame.seq,
        "topic_id": frame.topic_id,
        "ack_req": frame.ack_req,
        "payload": frame.payload,
    }


class BridgeForwardingTests(unittest.TestCase):
    def setUp(self) -> None:
        os.chdir(REPO_ROOT)
        self.cfg = load("PiNode/config.yaml")
        self.radio = StubRadio()
        self.mqtt = InMemoryMqttBridge()
        self.mqtt.connect()
        self.bridge = Bridge(self.cfg, self.radio, self.mqtt)
        self.bridge.start()

    def tearDown(self) -> None:
        self.bridge.stop()

    # ---------- forwarding for topic_id 10 (tele/HichiIR/STATE) ----------
    def test_forward_powermeter_status_to_lora_topic_10(self) -> None:
        clean_state = {
            "Time": "2026-09-12T14:13:29",
            "UptimeSec": 178413,
            "MqttCount": 3,
            "Wifi": {"RSSI": 50, "Signal": -75, "LinkCount": 2, "Downtime": "0T00:03:01"},
        }
        self.mqtt.inject("tele/HichiIR/STATE", json.dumps(clean_state))

        self.assertGreater(len(self.radio.sent), 0,
                           "no LoRa frame sent for tele/HichiIR/STATE")
        # Look for a frame with topic_id == 10
        frames = [_parse_lora_frame(d) for d in self.radio.sent]
        tid10 = [f for f in frames if f["topic_id"] == 10]
        self.assertEqual(len(tid10), 1,
                         f"expected exactly 1 frame for tid=10, got {len(tid10)} "
                         f"(all tids: {[f['topic_id'] for f in frames]})")
        # tel/HichiIR/STATE is NOT retained, ack_req comes from topic.reliable=true
        self.assertTrue(tid10[0]["ack_req"], "topic 10 must be reliable")

    def test_forward_powermeter_energy_to_lora_topic_12(self) -> None:
        energy = {
            "sn": {
                "Time": "2026-09-12T14:03:26",
                "EMH": {"E_in": 586.455, "E_out": 218.897, "Power": -3},
            },
            "ver": 1,
        }
        self.mqtt.inject("tasmota/discovery/483FDA50C720/sensors",
                         json.dumps(energy))

        frames = [_parse_lora_frame(d) for d in self.radio.sent]
        tid12 = [f for f in frames if f["topic_id"] == 12]
        self.assertEqual(len(tid12), 1,
                         f"expected exactly 1 frame for tid=12, got {len(tid12)}")

    def test_no_double_send_when_topic_has_both_router_and_forwarder(self) -> None:
        """Regression: topic 10 is both in topics.yaml (direction=to_gateway)
        and target of a forwarder rule.  It must send exactly once."""
        self.mqtt.inject("tele/HichiIR/STATE", json.dumps({
            "Time": "2026-09-12T14:13:29",
            "UptimeSec": 42,
            "MqttCount": 1,
            "Wifi": {"RSSI": 60, "Signal": -70, "LinkCount": 1, "Downtime": "0T00:00:10"},
        }))
        frames = [_parse_lora_frame(d) for d in self.radio.sent]
        tid10 = [f for f in frames if f["topic_id"] == 10]
        self.assertEqual(len(tid10), 1, "topic 10 must not be sent twice")

    def test_subscription_registered_at_startup(self) -> None:
        """Every configured source_topic must be subscribed after start()."""
        subs = [t for (t, _pat, _q) in self.mqtt._subscriptions]
        for expected in ("tele/HichiIR/STATE",
                         "tasmota/discovery/483FDA50C720/sensors",
                         "tele/HichiIR/SENSOR",
                         "homeassistant/sensor/MSA-280425440006/quick/state"):
            self.assertIn(expected, subs,
                          f"'{expected}' was NOT subscribed at bridge startup")

    def test_forward_powermeter_energy_to_lora_topic_12_retained(self) -> None:
        """Retained Tasmota discovery message must still be forwarded via
        the forwarder path (only the direct router-TX path skips retained)."""
        energy = {
            "sn": {
                "Time": "2026-09-12T14:03:26",
                "EMH": {"E_in": 586.455, "E_out": 218.897, "Power": -3},
            },
            "ver": 1,
        }
        self.mqtt.inject("tasmota/discovery/483FDA50C720/sensors",
                         json.dumps(energy), retain=True)

        frames = [_parse_lora_frame(d) for d in self.radio.sent]
        tid12 = [f for f in frames if f["topic_id"] == 12]
        self.assertEqual(len(tid12), 1,
                         "retained topic 12 must still be forwarded once")

    def test_output_engine_waits_for_ha_discovery_config(self) -> None:
        """When mqtt_output has a config_source but no retained message
        has been received on it, no publish must occur even after both
        trigger subscriptions received data."""
        self.mqtt.inject("tele/HichiIR/SENSOR",
                         json.dumps({"Time": "2026-01-01T00:00:00Z",
                                     "EMH": {"Power": -13}}))
        self.mqtt.inject("homeassistant/sensor/MSA-280425440006/quick/state",
                         json.dumps({"bat_p": -48.5}))

        target = self.cfg.mqtt_outputs[0].target_topic
        hits = [p for p in self.mqtt.published() if p[0].endswith(target)]
        self.assertEqual(hits, [],
                         "output engine must not publish without config_source")

    def test_output_engine_publishes_once_config_arrives(self) -> None:
        """Once the retained HA-discovery config is delivered and both
        triggers have fresh data, the output engine publishes."""
        import time
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

        # 1) HA-discovery config arrives (retained)
        self.mqtt.inject("homeassistant/number/MSA-280425440006/power_ctrl/config",
                         json.dumps({"min": -800, "max": 1000, "step": 0.1}),
                         retain=True)

        # 2) triggers arrive
        self.mqtt.inject("tele/HichiIR/SENSOR",
                         json.dumps({"Time": now, "EMH": {"Power": 100}}))
        self.mqtt.inject("homeassistant/sensor/MSA-280425440006/quick/state",
                         json.dumps({"bat_p": 50}))

        target = self.cfg.mqtt_outputs[0].target_topic
        hits = [p for p in self.mqtt.published() if p[0].endswith(target)]
        self.assertGreater(len(hits), 0,
                           "output engine must publish once config+triggers are present")


if __name__ == "__main__":
    unittest.main()
