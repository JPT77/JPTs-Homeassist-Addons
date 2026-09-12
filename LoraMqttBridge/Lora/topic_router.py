"""MQTT topic <-> LoRa Topic ID router (role-aware)."""

from __future__ import annotations

from typing import Iterable

from .config_loader import TopicMap


class TopicRouter:
    """Maps between LoRa topic IDs and MQTT topics.

    The router is role-aware: it resolves each topic's semantic
    direction (`to_gateway`, `from_node`, or the legacy `tx`/`rx`/`bidir`)
    to a local flow direction using :meth:`TopicMap.role_direction`.
    """

    def __init__(self, topics: Iterable[TopicMap], role: str = "pi_node"):
        self.role = role
        self._by_id: dict[int, TopicMap] = {}
        self._by_topic: dict[str, TopicMap] = {}
        for t in topics:
            self._by_id[t.id] = t
            if t.mqtt_topic:
                self._by_topic[t.mqtt_topic] = t

    def topic_by_id(self, tid: int) -> TopicMap | None:
        return self._by_id.get(tid)

    def id_by_topic(self, topic: str) -> TopicMap | None:
        return self._by_topic.get(topic)

    def local_direction(self, topic: TopicMap) -> str:
        return topic.role_direction(self.role)

    def subscribe_targets(self) -> list[tuple[str, int]]:
        """MQTT topics that must be subscribed locally so that new messages
        can be transmitted over LoRa (tx or bidir for this role).
        """
        result: list[tuple[str, int]] = []
        for t in self._by_topic.values():
            if self.local_direction(t) in ("tx", "bidir"):
                result.append((t.mqtt_topic, t.qos))
        return result
