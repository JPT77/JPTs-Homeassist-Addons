"""MQTT-Output Engine.

Drives the `mqtt_outputs` section of the config: on every new message on
any of its trigger subscriptions, the engine

  1. Extracts the configured input values (and their timestamps) from
     the latest cached payload of each named subscription.
  2. Validates each input (powermeter error value, timezone offset,
     max_age staleness) and augments the input with `error` / `stale`
     flags.
  3. Runs the configured jq expression against the assembled input
     object.
  4. Publishes the result to `target_topic`.

The engine keeps a small per-subscription cache of the latest parsed
JSON payload plus its reception timestamp.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .config_loader import MqttOutput, MqttSubscription

if TYPE_CHECKING:
    from .mqtt_client import MqttBridge

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Optional jq support (reuses helpers from mqtt_forwarder if available)
# --------------------------------------------------------------------------
try:
    from .mqtt_forwarder import _is_jq_expression, extract_query, run_jq  # noqa: F401
    _HAS_FORWARDER = True
except Exception:  # pragma: no cover - fallback path
    _HAS_FORWARDER = False

    def extract_query(data: Any, query: str | None) -> Any:  # type: ignore[no-redef]
        if not query or query in (".", "value", ""):
            return data
        normalized = re.sub(r"\[(\d+)\]", r".\1", query.lstrip("."))
        current = data
        for part in normalized.split("."):
            if current is None:
                return None
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, (list, tuple)):
                try:
                    current = current[int(part)]
                except (ValueError, IndexError):
                    return None
            else:
                return None
        return current

    def _is_jq_expression(query: str) -> bool:  # type: ignore[no-redef]
        return any(tok in query for tok in ("(", "|", "try", "catch", "if ", "def ", "["))

    def run_jq(expression: str, data: Any) -> Any:  # type: ignore[no-redef]
        raise RuntimeError("jq library not available")


# --------------------------------------------------------------------------
# Runtime state
# --------------------------------------------------------------------------
@dataclass
class _CachedMessage:
    payload: Any
    received_at: float


class MqttOutputEngine:
    """Manages all configured `mqtt_outputs` and their trigger caches."""

    def __init__(
        self,
        outputs: list[MqttOutput],
        subscriptions: list[MqttSubscription],
        mqtt: "MqttBridge",
    ):
        self.outputs = outputs
        self.mqtt = mqtt

        # subscription name -> source topic (needed to route incoming msgs)
        self._sub_topic: dict[str, str] = {}
        for sub in subscriptions:
            if sub.name:
                self._sub_topic[sub.name] = sub.source_topic

        # subscription name -> latest cached payload
        self._cache: dict[str, _CachedMessage] = {}

        # source topic -> list of subscription names using that topic
        self._topic_to_subs: dict[str, list[str]] = {}
        for name, topic in self._sub_topic.items():
            self._topic_to_subs.setdefault(topic, []).append(name)

        # trigger subscription name -> list of outputs it triggers
        self._trigger_index: dict[str, list[MqttOutput]] = {}
        for out in self.outputs:
            for trig in out.trigger:
                self._trigger_index.setdefault(trig, []).append(out)

        # For latched error flags per output
        self._latched_errors: dict[str, bool] = {}

    # ------------------------------------------------------------------ API
    def start(self) -> None:
        """Ensure every source topic used by any output is subscribed."""
        subscribed: set[str] = set()
        for trigger_name in self._trigger_index.keys():
            topic = self._sub_topic.get(trigger_name)
            if topic and topic not in subscribed:
                self.mqtt.subscribe(topic, qos=0)
                subscribed.add(topic)
        if self.outputs:
            log.info("MqttOutputEngine started: %d outputs, %d triggers",
                     len(self.outputs), len(self._trigger_index))

    def handle_message(self, topic: str, payload: bytes) -> bool:
        """Update caches for all subscriptions on `topic` and recompute
        every output whose trigger set contains at least one of them.

        Returns True if at least one subscription cached the message.
        """
        subs = self._topic_to_subs.get(topic)
        if not subs:
            return False

        parsed = self._parse_payload(payload)
        now = time.time()
        for sub_name in subs:
            self._cache[sub_name] = _CachedMessage(payload=parsed, received_at=now)

        triggered: list[MqttOutput] = []
        seen: set[int] = set()
        for sub_name in subs:
            for out in self._trigger_index.get(sub_name, []):
                if id(out) in seen:
                    continue
                seen.add(id(out))
                triggered.append(out)

        for out in triggered:
            try:
                self._run_output(out)
            except Exception:
                log.exception("mqtt_output '%s' failed", out.name)
        return True

    # -------------------------------------------------------------- internals
    def _parse_payload(self, raw: bytes) -> Any:
        try:
            text = raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            return raw
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return text

    def _extract(self, data: Any, query: str | None) -> Any:
        if not query:
            return data
        if _is_jq_expression(query):
            try:
                return run_jq(query, data)
            except Exception as exc:
                log.debug("jq extract failed (%s) on %r – falling back to dot notation", exc, query)
        return extract_query(data, query)

    def _assemble_inputs(self, out: MqttOutput) -> dict[str, dict]:
        """Assemble the input object passed to the jq expression.

        Result shape: `{ input_name: {value, timestamp, error, stale} }`.
        """
        now = time.time()
        assembled: dict[str, dict] = {}

        pm_error = out.validation.powermeter_error_value
        tz = out.validation.timezone
        tz_drift_seen = False

        for name, spec in out.inputs.items():
            cached = self._cache.get(spec.subscription)
            entry: dict[str, Any] = {
                "value": None,
                "timestamp": None,
                "error": False,
                "stale": False,
            }

            if cached is None:
                entry["error"] = True
                entry["stale"] = True
                assembled[name] = entry
                continue

            # Value extraction
            entry["value"] = self._extract(cached.payload, spec.value)

            # Timestamp extraction
            ts_source = spec.timestamp
            ts_value: float | None = None
            if ts_source == "$received_at" or ts_source is None:
                ts_value = cached.received_at
            else:
                raw_ts = self._extract(cached.payload, ts_source)
                ts_value = self._coerce_epoch(raw_ts)
            entry["timestamp"] = ts_value

            # powermeter_error_value check
            if pm_error is not None and entry["value"] == pm_error:
                entry["error"] = True

            # max_age staleness
            if spec.max_age is not None and ts_value is not None:
                if (now - ts_value) > float(spec.max_age):
                    entry["stale"] = True

            # timezone drift check (Pi clock vs payload timestamp)
            if tz is not None and ts_value is not None and ts_source not in (None, "$received_at"):
                drift = abs(now - ts_value)
                if drift > tz.max_offset:
                    entry["error"] = True
                    tz_drift_seen = True

            assembled[name] = entry

        # Timezone latch: once a tz-drift error occurs, keep flagging
        # every input with error=True until the process restarts.  Only
        # timezone drift triggers the latch, not missing subscriptions
        # or plain staleness.
        if tz and tz.on_error == "latch":
            latched = self._latched_errors.get(out.name, False)
            if tz_drift_seen:
                latched = True
            self._latched_errors[out.name] = latched
            if latched:
                for v in assembled.values():
                    v["error"] = True

        return assembled

    @staticmethod
    def _coerce_epoch(value: Any) -> float | None:
        """Best-effort conversion of a timestamp value to epoch seconds."""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                # ISO 8601, with or without trailing 'Z'
                from datetime import datetime, timezone
                text = value.strip()
                if text.endswith("Z"):
                    text = text[:-1] + "+00:00"
                dt = datetime.fromisoformat(text)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except Exception:
                pass
        return None

    def _run_output(self, out: MqttOutput) -> None:
        inputs = self._assemble_inputs(out)

        if not out.expression or out.expression.strip() == ".":
            result: Any = inputs
        else:
            try:
                result = run_jq(out.expression, inputs)
            except Exception as exc:
                log.warning("mqtt_output '%s' expression error: %s", out.name, exc)
                return

        if result is None:
            log.debug("mqtt_output '%s' produced None – skipping publish", out.name)
            return

        if isinstance(result, (dict, list)):
            payload = json.dumps(result).encode("utf-8")
        elif isinstance(result, bool):
            payload = (b"true" if result else b"false")
        else:
            payload = str(result).encode("utf-8")

        if not out.target_topic:
            log.debug("mqtt_output '%s' has no target_topic; result=%r", out.name, result)
            return

        self.mqtt.publish(out.target_topic, payload, qos=out.qos, retain=out.retained)
        log.info("mqtt_output '%s' -> %s: %s", out.name, out.target_topic, payload)
