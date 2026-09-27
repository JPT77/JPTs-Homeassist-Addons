"""Unit tests for lora_trainer."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Stub paho if not present
if "paho" not in sys.modules:
    sys.modules["paho"] = MagicMock()
    sys.modules["paho.mqtt"] = MagicMock()
    sys.modules["paho.mqtt.client"] = MagicMock()

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from Lora.config_loader import LoraConfig
from Lora.lora_trainer import (
    MAGIC,
    CONTROL_TID,
    PROBE_TID,
    build_lora_config,
    make_control,
    make_probe,
    parse_args,
    rank_result,
)
from Lora.protocol import Frame, FrameType


class LoraTrainerTests(unittest.TestCase):
    def test_make_control_frame(self) -> None:
        cfg = {"sf": 8, "bw": 125000, "cr": 6, "tx": 14, "packets": 5}
        raw = make_control(seq=42, cfg=cfg)
        frame = Frame.decode(raw)

        self.assertEqual(frame.ftype, FrameType.CONTROL)
        self.assertEqual(frame.topic_id, CONTROL_TID)
        self.assertEqual(frame.seq, 42)
        self.assertTrue(frame.ack_req)
        self.assertTrue(frame.payload.startswith(MAGIC))

        payload_json = json.loads(frame.payload[len(MAGIC):].decode())
        self.assertEqual(payload_json["sf"], 8)
        self.assertEqual(payload_json["packets"], 5)

    def test_make_probe_frame(self) -> None:
        raw = make_probe(seq=10, payload=b"TEST_PAYLOAD")
        frame = Frame.decode(raw)

        self.assertEqual(frame.ftype, FrameType.MQTT)
        self.assertEqual(frame.topic_id, PROBE_TID)
        self.assertEqual(frame.seq, 10)
        self.assertTrue(frame.ack_req)
        self.assertEqual(frame.payload, b"TEST_PAYLOAD")

    def test_build_lora_config(self) -> None:
        base = LoraConfig(spreading_factor=7, bandwidth_hz=125000, coding_rate=5, tx_power_dbm=22)
        new_cfg = build_lora_config(base, sf=9, bw=250000, cr=6, tx=14)

        self.assertEqual(new_cfg.spreading_factor, 9)
        self.assertEqual(new_cfg.bandwidth_hz, 250000)
        self.assertEqual(new_cfg.coding_rate, 6)
        self.assertEqual(new_cfg.tx_power_dbm, 14)
        # unedited properties stay intact
        self.assertEqual(new_cfg.frequency_hz, base.frequency_hz)
        self.assertEqual(new_cfg.pins, base.pins)

    def test_rank_result(self) -> None:
        r1 = {"success_pct": 100.0, "sf": 8, "tx": 14, "bw": 125000, "cr": 6}
        r2 = {"success_pct": 100.0, "sf": 7, "tx": 22, "bw": 125000, "cr": 6}
        r3 = {"success_pct": 95.0, "sf": 7, "tx": 14, "bw": 125000, "cr": 6}

        results = [r1, r2, r3]
        results.sort(key=rank_result)

        # 100% with SF7 comes before 100% with SF8, 95% comes last
        self.assertEqual(results[0], r2)
        self.assertEqual(results[1], r1)
        self.assertEqual(results[2], r3)

    def test_parse_args_master(self) -> None:
        args = parse_args(["master", "--sf", "7", "8", "--packets", "5", "--mock"])
        self.assertEqual(args.mode, "master")
        self.assertEqual(args.sf, [7, 8])
        self.assertEqual(args.packets, 5)
        self.assertTrue(args.mock)

    def test_parse_args_tests_alias(self) -> None:
        args = parse_args(["master", "--tests", "20"])
        self.assertEqual(args.packets, 20)

    def test_parse_args_responder(self) -> None:
        args = parse_args(["responder", "--mock"])
        self.assertEqual(args.mode, "responder")
        self.assertTrue(args.mock)


if __name__ == "__main__":
    unittest.main()
