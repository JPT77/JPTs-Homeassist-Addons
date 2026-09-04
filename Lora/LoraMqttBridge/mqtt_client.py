"""MQTT-Client-Wrapper um paho-mqtt mit Auto-Reconnect."""

from __future__ import annotations

import logging
from typing import Callable

import paho.mqtt.client as mqtt

from .config_loader import MqttConfig

log = logging.getLogger(__name__)


class MqttBridge:
    def __init__(self, cfg: MqttConfig, on_message: Callable[[str, bytes], None] | None = None):
        self.cfg = cfg
        self._on_message = on_message
        self._subscriptions: list[tuple[str, int]] = []
        self._client = mqtt.Client(
            client_id=cfg.client_id,
            protocol=mqtt.MQTTv311,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        if cfg.username:
            self._client.username_pw_set(cfg.username, cfg.password)
        if cfg.tls:
            self._client.tls_set()
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_msg
        self._client.on_disconnect = self._on_disconnect
        self._client.will_set(f"{cfg.client_id}/status", "offline", qos=1, retain=True)

    # ------------------------------------------------------------
    def connect(self) -> None:
        log.info("MQTT connect %s:%d as %s", self.cfg.host, self.cfg.port,
                 self.cfg.client_id)
        self._client.connect_async(self.cfg.host, self.cfg.port, self.cfg.keepalive)
        self._client.loop_start()

    def stop(self) -> None:
        try:
            self._client.publish(f"{self.cfg.client_id}/status", "offline",
                                 qos=1, retain=True).wait_for_publish(timeout=1)
        except Exception:
            pass
        self._client.loop_stop()
        self._client.disconnect()

    def subscribe(self, topic: str, qos: int = 0) -> None:
        self._subscriptions.append((topic, qos))
        if self._client.is_connected():
            self._client.subscribe(topic, qos)

    def publish(self, topic: str, payload: bytes | str, qos: int = 0, retain: bool = False) -> None:
        self._client.publish(topic, payload, qos=qos, retain=retain)

    def set_on_message(self, cb: Callable[[str, bytes], None]) -> None:
        self._on_message = cb

    # ------------------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        log.info("MQTT connected: %s", reason_code)
        for topic, qos in self._subscriptions:
            client.subscribe(topic, qos)
        client.publish(f"{self.cfg.client_id}/status", "online", qos=1, retain=True)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):
        log.warning("MQTT disconnected: %s", reason_code)

    def _on_msg(self, client, userdata, msg):
        if self._on_message:
            try:
                self._on_message(msg.topic, msg.payload)
            except Exception:
                log.exception("on_message Callback fehlgeschlagen für %s", msg.topic)
        else:
            log.debug("MQTT %s = %r (kein Handler)", msg.topic, msg.payload)
