"""MQTT-Topic ↔ LoRa Topic-ID Router."""

from __future__ import annotations

from typing import Iterable

from .config_loader import TopicMap


class TopicRouter:
    def __init__(self, topics: Iterable[TopicMap]):
        self._by_id: dict[int, TopicMap] = {}
        self._by_topic: dict[str, TopicMap] = {}
        for t in topics:
            self._by_id[t.id] = t
            self._by_topic[t.mqtt_topic] = t

    def topic_by_id(self, tid: int) -> TopicMap | None:
        return self._by_id.get(tid)

    def id_by_topic(self, topic: str) -> TopicMap | None:
        return self._by_topic.get(topic)

    def subscribe_targets(self) -> list[tuple[str, int]]:
        """Alle Topics, die aus MQTT gelesen werden (tx über LoRa oder bidir)."""
        return [
            (t.mqtt_topic, t.qos)
            for t in self._by_topic.values()
            if t.direction in ("tx", "bidir")
        ]
