#!/usr/bin/env python3
"""
LoRa SX1262 Trainer for JPTs-Homeassist-Addons.

Two modes:
  master     actively tests combinations and measures ACK success, RTT, RSSI, SNR
  responder waits for CONTROL test frames, switches radio parameters, and ACKs probes

Run on two Pi nodes using the same wiring/config as the existing bridge.

Examples:
  python3 lora_trainer.py master --config /config/config.yaml --tests 10
  python3 lora_trainer.py responder --config /config/config.yaml

The trainer intentionally uses the existing LoraRadio implementation, so the
actual SX1262 setup, DIO1 IRQ handling and RX/TX path stay identical to the bridge.
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import statistics
import sys
import time
from dataclasses import replace

from Lora.config_loader import load, LoraConfig
from Lora.lora_driver import LoraRadio, RxEvent
from Lora.protocol import (
    Frame, FrameType, PROTOCOL_VERSION, build_ack, build_mqtt
)

log = logging.getLogger("lora_trainer")

CONTROL_TID = 254
PROBE_TID = 253
SEQ_START = 1

# CONTROL payload:
# b"LT1" + JSON
MAGIC = b"LT1"


def make_control(seq: int, cfg: dict) -> bytes:
    body = MAGIC + json.dumps(cfg, separators=(",", ":")).encode()
    return Frame(PROTOCOL_VERSION, FrameType.CONTROL, seq, CONTROL_TID, body).encode()


def make_probe(seq: int, payload: bytes) -> bytes:
    return build_mqtt(seq, PROBE_TID, payload, ack_req=True).encode()


def wait_for_frame(radio: LoraRadio, seq: int, timeout: float) -> tuple[Frame, RxEvent] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        evt = radio.get_rx(timeout=min(0.1, max(0.01, deadline-time.monotonic())))
        if evt is None or not evt.ok:
            continue
        try:
            frame = Frame.decode(evt.payload)
        except ValueError:
            continue
        if frame.seq == seq:
            return frame, evt
    return None


def build_lora_config(base: LoraConfig, sf: int, bw: int, cr: int, tx: int) -> LoraConfig:
    return replace(
        base,
        spreading_factor=sf,
        bandwidth_hz=bw,
        coding_rate=cr,
        tx_power_dbm=tx,
    )


def open_radio(cfg: LoraConfig) -> LoraRadio:
    return LoraRadio(cfg)


def test_combo(base: LoraConfig, combo: tuple[int,int,int,int], packets: int,
               ack_timeout: float, warmup: float) -> dict:
    sf, bw, cr, tx = combo
    cfg = build_lora_config(base, sf, bw, cr, tx)
    radio = open_radio(cfg)
    try:
        radio.open()
        time.sleep(warmup)

        # This process is paired with a responder. The responder is already
        # switched to the same RF parameters before probes are sent.
        sent = 0
        ok = 0
        rtts = []
        rssis = []
        snrs = []

        for i in range(packets):
            seq = (SEQ_START + i) & 0xFF
            payload = f"LORA-TRAIN:{seq}".encode()
            t0 = time.monotonic()
            if not radio.send(make_probe(seq, payload)):
                continue
            sent += 1

            result = wait_for_frame(radio, seq, ack_timeout)
            if result is None:
                continue

            frame, evt = result
            if frame.ftype == FrameType.ACK and frame.ack_rsp:
                ok += 1
                rtts.append((time.monotonic() - t0) * 1000.0)
                rssis.append(evt.rssi)
                snrs.append(evt.snr)

        success = (ok / packets * 100.0) if packets else 0.0
        return {
            "sf": sf, "bw": bw, "cr": cr, "tx": tx,
            "packets": packets, "tx_done": sent, "acks": ok,
            "success_pct": round(success, 1),
            "rtt_ms": round(statistics.mean(rtts), 1) if rtts else None,
            "rtt_p95_ms": round(sorted(rtts)[max(0, int(len(rtts)*0.95)-1)], 1) if rtts else None,
            "rssi_dbm": round(statistics.mean(rssis), 1) if rssis else None,
            "snr_db": round(statistics.mean(snrs), 1) if snrs else None,
        }
    finally:
        radio.close()


def rank_result(r: dict) -> tuple:
    # Higher reliability first; then lower airtime proxy (SF); then lower TX.
    return (-r["success_pct"], r["sf"], r["tx"], r["bw"], r["cr"])


def master(args: argparse.Namespace) -> int:
    cfg = load(args.config)
    base = cfg.lora

    combinations = list(itertools.product(
        args.sf,
        args.bw,
        args.cr,
        args.tx,
    ))

    # Tell responder to switch before each RF test.
    control_radio = open_radio(base)
    results = []

    try:
        control_radio.open()
        print(f"Testing {len(combinations)} combinations, {args.packets} packets each")

        for idx, combo in enumerate(combinations, 1):
            sf, bw, cr, tx = combo
            print(f"[{idx}/{len(combinations)}] SF{sf} BW{bw//1000} CR4/{cr} TX{tx}dBm", flush=True)

            seq = (200 + idx) & 0xFF
            control = make_control(seq, {
                "sf": sf, "bw": bw, "cr": cr, "tx": tx,
                "packets": args.packets,
            })
            if not control_radio.send(control):
                print("  responder control TX failed")
                continue

            # Confirm that the responder received the control frame while
            # both sides are still on the stable/base RF settings.
            control_ack = wait_for_frame(control_radio, seq, args.ack_timeout)
            if control_ack is None or control_ack[0].ftype != FrameType.ACK:
                print("  responder did not ACK control")
                continue

            # Re-open master radio with the new settings.
            control_radio.close()
            result = test_combo(base, combo, args.packets, args.ack_timeout, args.warmup)
            control_radio = open_radio(base)
            control_radio.open()

            print(
                f"  {result['success_pct']:5.1f}%  "
                f"RSSI {result['rssi_dbm']}  SNR {result['snr_db']}  "
                f"RTT {result['rtt_ms']} ms"
            )
            results.append(result)

        results.sort(key=rank_result)
        print("\nRESULTS")
        print("SF  BW   CR   TX   OK       RSSI    SNR   RTT")
        for r in results:
            print(
                f"{r['sf']:2}  {r['bw']//1000:3}  4/{r['cr']}  "
                f"{r['tx']:2}  {r['success_pct']:5.1f}%  "
                f"{str(r['rssi_dbm']):>6}  {str(r['snr_db']):>5}  "
                f"{str(r['rtt_ms']):>6}"
            )

        qualified = [r for r in results if r["success_pct"] >= args.target]
        if qualified:
            # Choose the lowest SF first, then lowest TX while retaining target.
            chosen = sorted(
                qualified,
                key=lambda r: (r["sf"], r["tx"], r["bw"], r["cr"])
            )[0]
            print(
                f"\nTARGET >= {args.target:.1f}%: "
                f"SF{chosen['sf']} BW{chosen['bw']//1000}kHz "
                f"CR4/{chosen['cr']} TX{chosen['tx']}dBm"
            )
        else:
            print(f"\nNo combination reached target {args.target:.1f}%.")

        if args.json:
            print("\nJSON")
            print(json.dumps(results, indent=2))
    finally:
        control_radio.close()

    return 0


def responder(args: argparse.Namespace) -> int:
    cfg = load(args.config)
    radio = open_radio(cfg.lora)
    radio.open()
    current = cfg.lora
    probes_left = 0
    print("LoRa trainer responder ready")

    try:
        while True:
            evt = radio.get_rx(timeout=0.5)
            if evt is None or not evt.ok:
                continue
            try:
                frame = Frame.decode(evt.payload)
            except ValueError:
                continue

            if frame.ftype == FrameType.CONTROL and frame.topic_id == CONTROL_TID:
                if not frame.payload.startswith(MAGIC):
                    continue
                params = json.loads(frame.payload[len(MAGIC):].decode())
                packet_count = int(params.get("packets", 10))
                newcfg = build_lora_config(
                    cfg.lora, int(params["sf"]), int(params["bw"]),
                    int(params["cr"]), int(params["tx"])
                )
                print(
                    f"switch -> SF{newcfg.spreading_factor} "
                    f"BW{newcfg.bandwidth_hz} CR4/{newcfg.coding_rate} "
                    f"TX{newcfg.tx_power_dbm}"
                )

                # ACK control while still on old settings, then switch.
                radio.send(build_ack(frame).encode())

                radio.close()
                radio = open_radio(newcfg)
                radio.open()
                current = newcfg
                probes_left = packet_count
                continue

            if frame.ftype == FrameType.MQTT and frame.topic_id == PROBE_TID and frame.ack_req:
                radio.send(build_ack(frame).encode())
                probes_left -= 1
                if probes_left <= 0:
                    # Return to the stable/base channel so the master can
                    # issue the next CONTROL frame.
                    radio.close()
                    radio = open_radio(cfg.lora)
                    radio.open()
                    current = cfg.lora
    except KeyboardInterrupt:
        return 0
    finally:
        radio.close()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SX1262 LoRa parameter trainer")
    sub = p.add_subparsers(dest="mode", required=True)

    m = sub.add_parser("master")
    m.add_argument("--config", default=None)
    m.add_argument("--sf", nargs="+", type=int, default=[7,8,9,10,11,12])
    m.add_argument("--bw", nargs="+", type=int, default=[125000,250000,500000])
    m.add_argument("--cr", nargs="+", type=int, default=[5,6,7,8])
    m.add_argument("--tx", nargs="+", type=int, default=[2,6,10,14,18,22])
    m.add_argument("--packets", type=int, default=10)
    m.add_argument("--target", type=float, default=99.0)
    m.add_argument("--ack-timeout", type=float, default=3.0)
    m.add_argument("--warmup", type=float, default=0.15)
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=master)

    r = sub.add_parser("responder")
    r.add_argument("--config", default=None)
    r.set_defaults(func=responder)
    return p.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    sys.exit(args.func(args))
