"""LoRa <-> MQTT Payload Codec.

Encodes MQTT payloads into compact binary LoRa frames and decodes
received LoRa binary frames back into structured MQTT payloads (JSON)
using data types, field names, and transforms specified in the topic configuration.
"""

from __future__ import annotations

import json
import logging
import math
import re
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config_loader import FieldSpec, TopicMap

log = logging.getLogger(__name__)

# Base epoch for compact 32-bit LoRa timestamps: 2020-01-01T00:00:00Z
LORA_EPOCH: int = 1577836800

# Mapping of supported type names to struct format characters (little-endian)
TYPE_FORMATS: dict[str, str] = {
    "float": "<f",
    "float32": "<f",
    "double": "<d",
    "float64": "<d",
    "int8": "<b",
    "uint8": "<B",
    "byte": "<B",
    "int16": "<h",
    "uint16": "<H",
    "int": "<i",
    "int32": "<i",
    "uint": "<I",
    "uint32": "<I",
    "int64": "<q",
    "uint64": "<Q",
    "bool": "<?",
}

# ---------------------------------------------------------------------------
# Timestamp and Duration Conversion Helpers
# ---------------------------------------------------------------------------

def to_lora_time(val: Any) -> int:
    """Convert ISO8601 string, unix epoch number, or None to LoRa uint32 seconds since 2020-01-01."""
    if val is None or val == "":
        return 4294967295
    if isinstance(val, (int, float)):
        if val >= LORA_EPOCH:
            sec = int(val - LORA_EPOCH)
        else:
            sec = int(val)
        return max(0, min(4294967294, sec))
    if isinstance(val, str):
        try:
            s = val.strip()
            if not s.endswith("Z") and "+" not in s and "-" not in (s.split("T")[-1] if "T" in s else ""):
                s += "+00:00"
            elif s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            sec = int(dt.timestamp() - LORA_EPOCH)
            return max(0, min(4294967294, sec))
        except Exception:
            return 4294967295
    return 4294967295


def from_lora_time(val: Any) -> str | None:
    """Convert LoRa uint32 seconds since 2020-01-01 back to ISO8601 string."""
    if val is None or val == 4294967295:
        return None
    try:
        sec = int(val)
        if sec == 4294967295:
            return None
        dt = datetime.fromtimestamp(sec + LORA_EPOCH, timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def parse_duration_sec(val: Any) -> int | None:
    """Parse duration like '2T01:33:33', '00:01:09', or numeric string into integer seconds."""
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, str):
        m = re.match(r"(?:(\d+)T)?(\d+):(\d+):(\d+)", val.strip())
        if m:
            d, h, mi, s = m.groups()
            return (int(d or 0) * 86400) + (int(h) * 3600) + (int(mi) * 60) + int(s)
        try:
            return int(float(val.strip()))
        except Exception:
            return None
    return None


def extract_query(data: Any, query: str | None) -> Any:
    """Extract a value from nested data using dot notation and array indexing."""
    if not query or query in (".", "value", ""):
        return data

    normalized_query = re.sub(r"\[(\d+)\]", r".\1", query).lstrip(".")
    parts = normalized_query.split(".")

    current = data
    for part in parts:
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


def eval_transform_expr(expr: str, data: Any) -> Any:
    """Evaluate a transformation expression (jq or python fallback) against data."""
    if expr is None:
        return data

    expr_str = str(expr).strip()
    if not expr_str or expr_str == ".":
        return data

    # Try jq library if available
    try:
        from .mqtt_forwarder import _is_jq_expression, run_jq
        log.info(f"JQ {expr_str}")
        if _is_jq_expression(expr_str):
            try:
                return run_jq(expr_str, data)
            except Exception as exc:
                log.debug("jq evaluation failed (%s) for %r, using fallback", exc, expr_str)
    except Exception:
        pass

    # Pure Python evaluation fallback
    return _eval_python_fallback(expr_str, data)


def _split_top_level(text: str, delimiter: str) -> list[str] | None:
    """Split string by delimiter only when not nested inside parentheses."""
    parts: list[str] = []
    curr: list[str] = []
    depth = 0
    dlen = len(delimiter)
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
            curr.append(ch)
            i += 1
        elif ch == ")":
            depth -= 1
            curr.append(ch)
            i += 1
        elif depth == 0 and text[i : i + dlen] == delimiter:
            parts.append("".join(curr).strip())
            curr = []
            i += dlen
        else:
            curr.append(ch)
            i += 1
    if parts:
        parts.append("".join(curr).strip())
        return parts
    return None


def _is_enclosed_in_parens(s: str) -> bool:
    """Return True if the entire string is enclosed in a single pair of balanced parentheses."""
    if not (s.startswith("(") and s.endswith(")")):
        return False
    depth = 0
    for i, ch in enumerate(s):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and i < len(s) - 1:
                return False
    return depth == 0


def _eval_python_fallback(expr: str, data: Any) -> Any:
    """Pure-Python fallback evaluator for transform expressions."""
    if expr is None:
        return data

    expr_str = str(expr).strip()
    if not expr_str or expr_str == ".":
        return data

    # Unwrap outer balanced parentheses: ( ... )
    while _is_enclosed_in_parens(expr_str):
        expr_str = expr_str[1:-1].strip()

    # 1. Pipe `|` (left-to-right evaluation pipeline)
    pipe_parts = _split_top_level(expr_str, "|")
    if pipe_parts:
        curr_val = data
        for p in pipe_parts:
            curr_val = _eval_python_fallback(p, curr_val)
        return curr_val

    # 2. Alternative `//`
    alt_parts = _split_top_level(expr_str, "//")
    if alt_parts:
        for p in alt_parts:
            res = _eval_python_fallback(p, data)
            if res is not None and res != "":
                return res
        return None

    # 3. Arithmetic operators (+, -, *, /)
    for op in ("+", "-", "*", "/"):
        op_parts = _split_top_level(expr_str, f" {op} ")
        if op_parts and len(op_parts) == 2:
            left = _eval_python_fallback(op_parts[0], data)
            right = _eval_python_fallback(op_parts[1], data)
            if left is not None and right is not None:
                try:
                    l_num = float(left)
                    r_num = float(right)
                    if op == "+":
                        return l_num + r_num
                    if op == "-":
                        return l_num - r_num
                    if op == "*":
                        return l_num * r_num
                    if op == "/":
                        return (l_num / r_num) if r_num != 0 else 0
                except (ValueError, TypeError):
                    pass
            return None

    # 4. Built-in filter functions
    if expr_str == "to_lora_time":
        return to_lora_time(data)
    if expr_str == "from_lora_time":
        return from_lora_time(data)
    if expr_str == "duration_sec":
        return parse_duration_sec(data)
    if expr_str == "round":
        try:
            return round(float(data))
        except (ValueError, TypeError):
            return data
    if expr_str == "floor":
        try:
            return math.floor(float(data))
        except (ValueError, TypeError):
            return data

    # 5. Literal number
    try:
        if "." in expr_str:
            return float(expr_str)
        return int(expr_str)
    except ValueError:
        pass

    # 6. Dot path lookup (e.g. .Wifi.Signal, .Time, .uptime)
    return extract_query(data, expr_str)


# ---------------------------------------------------------------------------
# PayloadCodec
# ---------------------------------------------------------------------------

class PayloadCodec:
    """Encodes and decodes topic payloads between MQTT and LoRa binary formats."""

    def encode(self, topic: TopicMap, data: Any) -> bytes:
        """Encode MQTT payload into compact LoRa binary bytes.

        If no fields are configured for the topic, the raw data is returned
        as bytes for backward compatibility.
        """
        log.info(f"topic.fields={topic.fields}")
        if not topic.fields:
            if isinstance(data, bytes):
                log.info(f"return {data}")
                return data
            if isinstance(data, str):
                log.info(f"return {data.encode("utf-8")}")
                return data.encode("utf-8")
            log.info(f"return {data.encode("utf-8")}")
            return str(data).encode("utf-8")

        values = self._prepare_encode_values(topic, data)
        packed_parts: list[bytes] = []

        total_fields = len(topic.fields)
        log.info(f"total_fields {total_fields}")
        for idx, field in enumerate(topic.fields):
            log.info(f"field {idx}: {field}")
            val = values.get(field.name)
            log.info(f"val: {val}")
            if val is None and total_fields == 1 and len(values) == 1:
                val = next(iter(values.values()))
            if val is None:
                raise ValueError(
                    f"Missing value for field '{field.name}' in topic '{topic.mqtt_topic}'"
                )

            is_last = idx == total_fields - 1
            packed_parts.append(self._pack_field(field, val, is_last=is_last))

        return b"".join(packed_parts)

    def decode(self, topic: TopicMap, raw: bytes) -> bytes:
        """Decode LoRa binary bytes back into an MQTT JSON payload.

        Reconstructs field names and values into a JSON-encoded byte string.
        If transform.lora2mqtt is configured, transforms unpacked fields into
        the target MQTT structure. If no fields are configured, raw bytes are returned.
        """
        if not topic.fields:
            return raw

        unpacked = self.decode_to_dict(topic, raw)
        transformed = self._apply_lora2mqtt(topic, unpacked)
        return json.dumps(transformed).encode("utf-8")

    def decode_to_dict(self, topic: TopicMap, raw: bytes) -> dict[str, Any]:
        """Decode LoRa binary bytes into a flat dictionary of {field_name: value}."""
        if not topic.fields:
            return {}

        result: dict[str, Any] = {}
        offset = 0
        total_fields = len(topic.fields)

        for idx, field in enumerate(topic.fields):
            is_last = idx == total_fields - 1
            val, new_offset = self._unpack_field(field, raw, offset, is_last=is_last)
            result[field.name] = val
            offset = new_offset

        return result

    def decode_to_mqtt_dict(self, topic: TopicMap, raw: bytes) -> dict[str, Any]:
        """Decode LoRa binary bytes and apply lora2mqtt transform to produce target MQTT dict."""
        if not topic.fields:
            return {}
        unpacked = self.decode_to_dict(topic, raw)
        return self._apply_lora2mqtt(topic, unpacked)

    def _prepare_encode_values(self, topic: TopicMap, data: Any) -> dict[str, Any]:
        """Extract and transform MQTT data into binary field values."""
        parsed_data = self._parse_input_payload(data)

        # Apply mqtt2lora transform if defined
        if topic.transform and topic.transform.mqtt2lora:
            values: dict[str, Any] = {}
            for field_name, expr in topic.transform.mqtt2lora.items():
                log.info(f"field {field_name}, expr {expr}")
                val = eval_transform_expr(expr, parsed_data)
                if val is not None:
                    values[field_name] = val

            # Also check if any unmapped fields can be extracted directly from data
            if isinstance(parsed_data, dict):
                for f in topic.fields:
                    if f.name not in values and f.name in parsed_data:
                        values[f.name] = parsed_data[f.name]

            return values

        # Fallback to normalized input values
        return self._normalize_input_values(topic, parsed_data)

    def _apply_lora2mqtt(self, topic: TopicMap, unpacked: dict[str, Any]) -> dict[str, Any]:
        """Apply lora2mqtt transform to unpacked field values."""
        if not topic.transform or not topic.transform.lora2mqtt:
            return unpacked

        def _eval_structure(template: Any) -> Any:
            if isinstance(template, dict):
                return {k: _eval_structure(v) for k, v in template.items()}
            if isinstance(template, list):
                return [_eval_structure(item) for item in template]
            if isinstance(template, str):
                return eval_transform_expr(template, unpacked)
            return template

        result = _eval_structure(topic.transform.lora2mqtt)
        return result if isinstance(result, dict) else {"value": result}

    def _parse_input_payload(self, data: Any) -> Any:
        """Parse raw bytes or JSON string into Python objects."""
        if isinstance(data, (bytes, bytearray)):
            try:
                text = data.decode("utf-8").strip()
            except UnicodeDecodeError:
                return data
        elif isinstance(data, str):
            text = data.strip()
        else:
            return data

        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return text

    def _normalize_input_values(self, topic: TopicMap, data: Any) -> dict[str, Any]:
        """Extract a dictionary of {field_name: value} from various input formats."""
        if isinstance(data, dict):
            return data

        if isinstance(data, (bytes, bytearray)):
            try:
                text = data.decode("utf-8").strip()
            except UnicodeDecodeError:
                text = ""
        elif isinstance(data, str):
            text = data.strip()
        else:
            text = None

        if text is not None:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
                if len(topic.fields) == 1:
                    return {topic.fields[0].name: parsed}
            except (json.JSONDecodeError, ValueError):
                pass

            if len(topic.fields) == 1:
                return {topic.fields[0].name: text}

            raise ValueError(
                f"Expected JSON object for multi-field topic '{topic.mqtt_topic}', got: {text!r}"
            )

        if len(topic.fields) == 1:
            return {topic.fields[0].name: data}

        raise ValueError(
            f"Expected dict or JSON object for multi-field topic '{topic.mqtt_topic}', got {type(data).__name__}"
        )

    def _pack_field(self, field: FieldSpec, val: Any, is_last: bool = False) -> bytes:
        """Pack a single field value into binary format."""
        ftype = field.type.lower()

        if ftype in ("str", "string"):
            str_bytes = str(val).encode("utf-8")
            if is_last:
                return str_bytes
            if len(str_bytes) > 255:
                raise ValueError(
                    f"String length {len(str_bytes)} exceeds maximum of 255 for field '{field.name}'"
                )
            return struct.pack("<B", len(str_bytes)) + str_bytes

        fmt = TYPE_FORMATS.get(ftype)
        if fmt is None:
            raise ValueError(f"Unsupported field type: '{field.type}' for field '{field.name}'")

        try:
            if "f" in fmt or "d" in fmt:
                return struct.pack(fmt, float(val))
            if "?" in fmt:
                return struct.pack(fmt, bool(val))
            # Integer types: convert safely even if val was float string or float
            return struct.pack(fmt, int(round(float(val))))
        except struct.error as exc:
            raise ValueError(
                f"Failed to pack value '{val}' as '{field.type}' for field '{field.name}': {exc}"
            ) from exc

    def _unpack_field(
        self, field: FieldSpec, raw: bytes, offset: int, is_last: bool = False
    ) -> tuple[Any, int]:
        """Unpack a single field value from raw bytes at offset."""
        ftype = field.type.lower()

        if ftype in ("str", "string"):
            if is_last:
                str_val = raw[offset:].decode("utf-8", errors="replace")
                return str_val, len(raw)
            if offset >= len(raw):
                raise ValueError(
                    f"Buffer underrun unpacking string length for field '{field.name}'"
                )
            str_len = struct.unpack_from("<B", raw, offset)[0]
            offset += 1
            if offset + str_len > len(raw):
                raise ValueError(
                    f"Buffer underrun unpacking string data for field '{field.name}'"
                )
            str_val = raw[offset : offset + str_len].decode("utf-8", errors="replace")
            return str_val, offset + str_len

        fmt = TYPE_FORMATS.get(ftype)
        if fmt is None:
            raise ValueError(f"Unsupported field type: '{field.type}' for field '{field.name}'")

        size = struct.calcsize(fmt)
        if offset + size > len(raw):
            raise ValueError(
                f"Buffer underrun unpacking field '{field.name}' of type '{field.type}' "
                f"(need {size} bytes at offset {offset}, total len {len(raw)})"
            )

        val = struct.unpack_from(fmt, raw, offset)[0]
        if ftype in ("float", "float32"):
            val = round(val, 4)
        elif ftype in ("double", "float64"):
            val = round(val, 6)

        return val, offset + size
