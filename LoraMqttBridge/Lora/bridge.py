"""Bridge-Kern: verbindet Radio, ACK-Manager, MQTT und Topic-Router."""

from __future__ import annotations

import logging
import threading
import time

from .ack_manager import AckManager
from .config_loader import Config
from .lora_driver import LoraRadio
from .mqtt_client import MqttBridge
from .protocol import Frame, FrameType, PROTOCOL_VERSION, build_ack, build_mqtt
from .topic_router import TopicRouter

log = logging.getLogger(__name__)


class Bridge:
    """Verbindet LoRa <-> MQTT anhand des Topic-Routers.

    - MQTT-Messages auf konfigurierten TX-Topics werden zu LoRa-Frames.
    - LoRa-Frames vom Typ MQTT werden auf konfigurierten RX-Topics gepublisht.
    - ACK-Frames werden an den AckManager weitergereicht.
    - Duplikate (retry) werden am (topic_id, seq)-Paar erkannt.
    """

    def __init__(self, cfg: Config, radio: LoraRadio, mqtt: MqttBridge):
        self.cfg = cfg
        self.radio = radio
        self.mqtt = mqtt
        self.router = TopicRouter(cfg.topics)
        self.ack = AckManager(cfg.ack, sender=self._raw_send)
        self._seen: dict[tuple[int, int], float] = {}
        self._stop = threading.Event()
        self._rx_thread: threading.Thread | None = None

    # ------------------------------------------------------------
    def start(self) -> None:
        self.ack.start()
        # MQTT subscriptions für TX-Richtung
        for topic, qos in self.router.subscribe_targets():
            self.mqtt.subscribe(topic, qos)
        self.mqtt.set_on_message(self._on_mqtt)
        self._rx_thread = threading.Thread(target=self._rx_loop,
                                           name="lora-rx", daemon=True)
        self._rx_thread.start()
        log.info("Bridge gestartet: %d Topics, ACK-Manager läuft",
                 len(self.cfg.topics))

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
        entry = self.router.id_by_topic(topic)
        if entry is None:
            return
        if entry.direction not in ("tx", "bidir"):
            return
        seq = self.ack.next_seq()
        frame = build_mqtt(seq, entry.id, payload,
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
            log.warning("Kein Topic-Mapping für ID %d", frame.topic_id)
            return
        if entry.direction not in ("rx", "bidir"):
            log.debug("Topic %s ist %s, RX-Frame ignoriert", entry.mqtt_topic, entry.direction)
            return
        self.mqtt.publish(entry.mqtt_topic, frame.payload,
                          qos=entry.qos, retain=entry.retained)

    def _gc_seen(self, now: float) -> None:
        if len(self._seen) < 1024:
            return
        cutoff = now - 60.0
        self._seen = {k: v for k, v in self._seen.items() if v >= cutoff}

    # ------------------------------------------------------------ helpers for other tasks
    def send_mqtt_over_lora(self, topic_id: int, payload: bytes, reliable: bool) -> None:
        seq = self.ack.next_seq()
        frame = build_mqtt(seq, topic_id, payload, ack_req=reliable)
        if reliable:
            self.ack.send_reliable(frame)
        else:
            self.ack.send_fire_and_forget(frame)
