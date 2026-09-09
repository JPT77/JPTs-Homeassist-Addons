"""MQTT Subscription and Forwarding Engine.

Subscribes to local MQTT topics, extracts values using JSON queries or jq expressions,
and forwards them either over LoRa (via Topic IDs) or to another local MQTT topic.

When json_query is a multi-line string (jq expression), it is executed via the
`jq` Python library together with the dataconvert.jq helper library.  The query
is expected to return a flat JSON array of integers (0-255), which is forwarded
directly as a raw LoRa payload – bypassing PayloadCodec entirely.

When json_query is a simple dot-notation string (e.g. "battery.soc") or the
`extract` dict is used, the classic extraction path is taken and PayloadCodec
encodes the result according to the field types in topics.yaml.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config_loader import MqttSubscription

if TYPE_CHECKING:
    from .mqtt_client import MqttBridge

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# jq library support
# ---------------------------------------------------------------------------

# Load the dataconvert.jq library once at module import time.
# It is prepended to every jq expression that contains one of the custom
# functions (u8, u16, u32, i8, i16, i32, be16, be32, duration_sec).
_DATACONVERT_PATH = Path(__file__).parent / "dataconvert.jq"
_DATACONVERT_LIB: str = ""

try:
    import jq as _jq_lib  # pip install jq

    if _DATACONVERT_PATH.is_file():
        _DATACONVERT_LIB = _DATACONVERT_PATH.read_text()
    else:
        log.warning("dataconvert.jq not found at %s – custom jq functions unavailable", _DATACONVERT_PATH)

    _JQ_AVAILABLE = True
except ImportError:
    _jq_lib = None  # type: ignore[assignment]
    _JQ_AVAILABLE = False
    log.warning("Python 'jq' library not installed – jq expressions in mqtt_subscriptions will not work")


def _is_jq_expression(query: str) -> bool:
    """Return True if the query looks like a jq expression (not simple dot notation)."""
    jq_indicators = ("(", "|", "try", "catch", "if ", "def ", "[", "fromdateiso8601")
    return any(tok in query for tok in jq_indicators)


def run_jq(expression: str, data: Any) -> Any:
    """Execute a jq expression against data, prepending the dataconvert library.

    Returns the jq output (typically a list of ints for LoRa payloads, or a scalar).
    Raises RuntimeError on jq compile/execution errors.
    """
    if not _JQ_AVAILABLE:
        raise RuntimeError("Python 'jq' library is not installed (pip install jq)")

    full_expr = f"{_DATACONVERT_LIB}\n{expression}" if _DATACONVERT_LIB else expression
    try:
        result = _jq_lib.first(full_expr, data)
        return result
    except Exception as exc:
        raise RuntimeError(f"jq error: {exc}") from exc


# ---------------------------------------------------------------------------
# Simple dot-notation extractor (no jq dependency)
# ---------------------------------------------------------------------------

def extract_query(data: Any, query: str | None) -> Any:
    """Extract a value from nested data using dot notation and array indexing.

    Examples:
        - "temperature" -> data["temperature"]
        - "battery.soc" -> data["battery"]["soc"]
        - "sensors[0].value" or "sensors.0.value" -> data["sensors"][0]["value"]
    """
    if not query or query in (".", "value", ""):
        return data

    # Normalize array index notation: foo[0] -> foo.0
    normalized_query = re.sub(r"\[(\d+)\]", r".\1", query)
    parts = normalized_query.split(".")

    current = data
    for part in parts:
        if current is None:
            return None

        if isinstance(current, dict):
            if part in current:
                current = current[part]
            else:
                return None
        elif isinstance(current, (list, tuple)):
            try:
                idx = int(part)
                current = current[idx]
            except (ValueError, IndexError):
                return None
        else:
            return None

    return current


# ---------------------------------------------------------------------------
# Main forwarder class
# ---------------------------------------------------------------------------

class MqttForwarder:
    """Manages local MQTT subscriptions, evaluates JSON queries, and forwards data."""

    def __init__(self, subscriptions: list[MqttSubscription], bridge: Any, mqtt: MqttBridge):
        self.subscriptions = subscriptions
        self.bridge = bridge
        self.mqtt = mqtt
        self._subs_by_topic: dict[str, list[MqttSubscription]] = {}

        for sub in self.subscriptions:
            if sub.source_topic:
                self._subs_by_topic.setdefault(sub.source_topic, []).append(sub)

    def start(self) -> None:
        """Subscribe to all configured source topics on the local MQTT broker."""
        for topic, subs in self._subs_by_topic.items():
            max_qos = max(s.qos for s in subs)
            self.mqtt.subscribe(topic, qos=max_qos)
            log.info("MqttForwarder subscribed to local MQTT topic '%s' (QoS %d)", topic, max_qos)

    def handle_message(self, topic: str, payload: bytes) -> bool:
        """Handle incoming message on subscribed topic.

        Returns True if matched by at least one forwarder subscription.
        """
        subs = self._subs_by_topic.get(topic)
        if not subs:
            return False

        parsed_data = self._parse_payload(payload)

        for sub in subs:
            try:
                self._process_subscription(sub, parsed_data, raw_payload=payload)
            except Exception as exc:
                log.warning(
                    "Error executing MQTT subscription '%s' on topic '%s': %s",
                    sub.name or sub.source_topic,
                    topic,
                    exc,
                )
        return True

    def _parse_payload(self, raw: bytes) -> Any:
        """Parse raw payload bytes into JSON object or scalar string/number."""
        try:
            text = raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            return raw

        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return text

    def _process_subscription(self, sub: MqttSubscription, parsed: Any, raw_payload: bytes) -> None:
        # ------------------------------------------------------------------
        # 1. Extract / transform field values
        # ------------------------------------------------------------------
        raw_bytes: bytes | None = None  # set when jq produces the final byte array

        if sub.json_query and _is_jq_expression(sub.json_query):
            # --- jq path: query returns a flat int array (the LoRa payload) ---
            result = run_jq(sub.json_query, parsed)
            if isinstance(result, list) and all(isinstance(b, int) for b in result):
                raw_bytes = bytes(result)
            else:
                # Unexpected result type – treat as extracted scalar/dict
                extracted: Any = result
        elif sub.extract:
            extracted = {
                field_name: extract_query(parsed, query_str)
                for field_name, query_str in sub.extract.items()
            }
        elif sub.json_query:
            extracted = extract_query(parsed, sub.json_query)
            if extracted is None:
                log.debug(
                    "Subscription '%s': query '%s' returned None on topic '%s'",
                    sub.name,
                    sub.json_query,
                    sub.source_topic,
                )
                return
        else:
            extracted = parsed

        # ------------------------------------------------------------------
        # 2. Forward to LoRa
        # ------------------------------------------------------------------
        if sub.target_topic_id is not None:
            if raw_bytes is not None:
                # jq produced the complete binary payload – send raw bytes
                self.bridge.send_raw_lora(sub.target_topic_id, raw_bytes, reliable=(sub.qos >= 1))
                log.info(
                    "Forwarder '%s': MQTT '%s' -> LoRa topic ID %d: %d raw bytes",
                    sub.name or "sub",
                    sub.source_topic,
                    sub.target_topic_id,
                    len(raw_bytes),
                )
            else:
                # Classic path: PayloadCodec encodes via topics.yaml field types
                self.bridge.send_mqtt_over_lora(
                    sub.target_topic_id,
                    extracted,
                    reliable=(sub.qos >= 1),
                )
                log.info(
                    "Forwarder '%s': MQTT '%s' -> LoRa topic ID %d: %r",
                    sub.name or "sub",
                    sub.source_topic,
                    sub.target_topic_id,
                    extracted,
                )

        # ------------------------------------------------------------------
        # 3. Forward to local MQTT topic
        # ------------------------------------------------------------------
        if sub.target_mqtt_topic:
            value_to_publish = raw_bytes if raw_bytes is not None else extracted
            if sub.payload_template and raw_bytes is None:
                val_repr = json.dumps(value_to_publish) if isinstance(value_to_publish, (dict, list)) else str(value_to_publish)
                if "{value}" in sub.payload_template:
                    out_str = sub.payload_template.replace("{value}", val_repr)
                else:
                    try:
                        out_str = sub.payload_template.format(value=val_repr)
                    except (KeyError, IndexError, ValueError):
                        out_str = sub.payload_template
                out_bytes = out_str.encode("utf-8")
            elif isinstance(value_to_publish, bytes):
                out_bytes = value_to_publish
            elif isinstance(value_to_publish, (dict, list)):
                out_bytes = json.dumps(value_to_publish).encode("utf-8")
            else:
                out_bytes = str(value_to_publish).encode("utf-8")

            self.mqtt.publish(
                sub.target_mqtt_topic,
                out_bytes,
                qos=sub.qos,
                retain=sub.retained,
            )
            log.info(
                "Forwarder '%s': MQTT '%s' -> MQTT '%s': %r",
                sub.name or "sub",
                sub.source_topic,
                sub.target_mqtt_topic,
                out_bytes,
            )
