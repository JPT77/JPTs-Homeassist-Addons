"""Bridge-Kern: verbindet Radio, ACK-Manager, MQTT und Topic-Router."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .ack_manager import AckManager
from .config_loader import Config
from .lora_driver import LoraRadio
from .mqtt_client import MqttBridge
from .mqtt_forwarder import MqttForwarder
from .payload_codec import PayloadCodec
from .protocol import Frame, FrameType, PROTOCOL_VERSION, build_ack, build_mqtt
from .topic_router import TopicRouter

log = logging.getLogger(__name__)


class Bridge:
    """Connects LoRa <-> MQTT using the topic router, payload codec, and forwarder.

    - MQTT messages on configured TX topics are encoded into LoRa binary frames.
    - LoRa frames of type MQTT are decoded and published to configured RX topics.
    - MQTT subscriptions can query JSON and forward to LoRa or local MQTT.
    - ACK frames are forwarded to the AckManager.
    - Duplicates (retry) are filtered based on the (topic_id, seq) pair.
    """

    def __init__(self, cfg: Config, radio: LoraRadio, mqtt: MqttBridge):
        self.cfg = cfg
        self.radio = radio
        self.mqtt = mqtt
        self.router = TopicRouter(cfg.topics, role=cfg.role)
        self.codec = PayloadCodec()
        self.forwarder = MqttForwarder(cfg.mqtt_subscriptions, bridge=self, mqtt=self.mqtt)
        self.ack = AckManager(cfg.ack, sender=self._raw_send)
        self._seen: dict[tuple[int, int], float] = {}
        self._stop = threading.Event()
        self._rx_thread: threading.Thread | None = None

    # ------------------------------------------------------------
    def start(self) -> None:
        self.ack.start()
        # MQTT subscriptions for TX direction from topic router
        for topic, qos in self.router.subscribe_targets():
            self.mqtt.subscribe(topic, qos)
        # MQTT subscriptions from configured forwarder rules
        self.forwarder.start()
        self.mqtt.set_on_message(self._on_mqtt)
        self._rx_thread = threading.Thread(target=self._rx_loop,
                                           name="lora-rx", daemon=True)
        self._rx_thread.start()
        log.info("Bridge gestartet: %d Topics, %d Forwarder-Subscriptions, ACK-Manager läuft",
                 len(self.cfg.topics), len(self.cfg.mqtt_subscriptions))

    def stop(self) -> None:
        self._stop.set()
        self.ack.stop()
        if self._rx_thread:
            self._rx_thread.join(timeout=2)

    # ------------------------------------------------------------
    def _raw_send(self, frame: Frame) -> bool:
        try:
            data = frame.encode()
        except ValueError as exc:
            log.error("Frame-Encode Fehler: %s", exc)
            return False
        return self.radio.send(data)

    # ------------------------------------------------------------
    def _on_mqtt(self, topic: str, payload: bytes) -> None:
        # Process any configured local forwarder subscription rules
        self.forwarder.handle_message(topic, payload)

        log.info(f"_on_mqtt(self, {str}, {payload})")
        entry = self.router.id_by_topic(topic)
        log.info(f"entry: {entry}")
        if entry is None:
            return
        if self.router.local_direction(entry) not in ("tx", "bidir"):
            return
        try:
            lora_payload = self.codec.encode(entry, payload)
        except Exception as exc:
            log.exception("Failed to encode MQTT payload for topic '%s': %s", topic, exc)
            return
        seq = self.ack.next_seq()
        frame = build_mqtt(seq, entry.id, lora_payload,
                           ack_req=(entry.qos >= 1))
        if frame.ack_req:
            self.ack.send_reliable(frame)
        else:
            self.ack.send_fire_and_forget(frame)

    # ------------------------------------------------------------
    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            evt = self.radio.get_rx(timeout=0.2)
            if evt is None:
                continue
            if not evt.ok:
                log.debug("RX-BAD irq=0x%04X (CRC/HeaderErr)", evt.irq_bits)
                continue
            try:
                frame = Frame.decode(evt.payload)
            except ValueError as exc:
                log.debug("Decode-Fehler: %s (%d B)", exc, len(evt.payload))
                continue
            if frame.version != PROTOCOL_VERSION:
                log.debug("Unbekannte Protokollversion %d", frame.version)
                continue

            log.info("RX %s rssi=%d snr=%.1f", frame, evt.rssi, evt.snr)

            if frame.ftype == FrameType.ACK:
                self.ack.on_ack(frame)
                continue

            # Dedup: bereits gesehene (topic_id, seq) → nur ACK-en
            key = (frame.topic_id, frame.seq)
            now = time.time()
            duplicate = key in self._seen and (now - self._seen[key]) < 30.0
            self._seen[key] = now
            self._gc_seen(now)

            if frame.ack_req:
                self._raw_send(build_ack(frame))

            if duplicate:
                log.debug("Dup gefiltert %s", frame)
                continue

            if frame.ftype == FrameType.MQTT:
                self._deliver_mqtt(frame)
            elif frame.ftype == FrameType.HELLO:
                log.info("HELLO von tid=%d: %r", frame.topic_id, frame.payload)
            elif frame.ftype == FrameType.CONTROL:
                log.info("CONTROL tid=%d: %r", frame.topic_id, frame.payload)

    def _deliver_mqtt(self, frame: Frame) -> None:
        entry = self.router.topic_by_id(frame.topic_id)
        if entry is None:
            log.warning("No topic mapping for ID %d", frame.topic_id)
            return
        if entry.direction not in ("rx", "bidir"):
            log.debug("Topic %s is %s, RX frame ignored", entry.mqtt_topic, entry.direction)
            return
        try:
            mqtt_payload = self.codec.decode(entry, frame.payload)
        except Exception as exc:
            log.warning("Failed to decode LoRa payload for topic ID %d: %s", frame.topic_id, exc)
            return
        self.mqtt.publish(entry.mqtt_topic, mqtt_payload,
                          qos=entry.qos, retain=entry.retained)

    def _gc_seen(self, now: float) -> None:
        if len(self._seen) < 1024:
            return
        cutoff = now - 60.0
        self._seen = {k: v for k, v in self._seen.items() if v >= cutoff}

    # ------------------------------------------------------------ helpers for other tasks
    def send_mqtt_over_lora(self, topic_id: int, payload: Any, reliable: bool) -> None:
        """Encode payload via PayloadCodec and send over LoRa for a given topic ID."""
        entry = self.router.topic_by_id(topic_id)
        if entry is not None:
            try:
                lora_payload = self.codec.encode(entry, payload)
            except Exception as exc:
                log.error("Failed to encode payload for topic ID %d: %s", topic_id, exc)
                return
        elif isinstance(payload, bytes):
            lora_payload = payload
        elif isinstance(payload, str):
            lora_payload = payload.encode("utf-8")
        else:
            lora_payload = str(payload).encode("utf-8")

        seq = self.ack.next_seq()
        frame = build_mqtt(seq, topic_id, lora_payload, ack_req=reliable)
        if reliable:
            self.ack.send_reliable(frame)
        else:
            self.ack.send_fire_and_forget(frame)

    def send_raw_lora(self, topic_id: int, payload: bytes, reliable: bool) -> None:
        """Send a pre-built byte array over LoRa, bypassing PayloadCodec entirely.

        Used when a jq expression already produces the final binary payload
        (e.g. via dataconvert.jq helpers u8/i8/be16/be32).
        """
        seq = self.ack.next_seq()
        frame = build_mqtt(seq, topic_id, payload, ack_req=reliable)
        if reliable:
            self.ack.send_reliable(frame)
        else:
            self.ack.send_fire_and_forget(frame)
