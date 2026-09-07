"""LoRa <-> MQTT Payload Codec.

Encodes MQTT payloads into compact binary LoRa frames and decodes
received LoRa binary frames back into structured MQTT payloads (JSON)
using data types and field names specified in the topic configuration.
"""

from __future__ import annotations

import json
import struct
from typing import Any

from .config_loader import FieldSpec, TopicMap


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


class PayloadCodec:
    """Encodes and decodes topic payloads between MQTT and LoRa binary formats."""

    def encode(self, topic: TopicMap, data: Any) -> bytes:
        """Encode MQTT payload into compact LoRa binary bytes.

        If no fields are configured for the topic, the raw data is returned
        as bytes for backward compatibility.
        """
        if not topic.fields:
            if isinstance(data, bytes):
                return data
            if isinstance(data, str):
                return data.encode("utf-8")
            return str(data).encode("utf-8")

        values = self._normalize_input_values(topic, data)
        packed_parts: list[bytes] = []

        total_fields = len(topic.fields)
        for idx, field in enumerate(topic.fields):
            val = values.get(field.name)
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
        If no fields are configured, raw bytes are returned.
        """
        if not topic.fields:
            return raw

        data_dict = self.decode_to_dict(topic, raw)
        return json.dumps(data_dict).encode("utf-8")

    def decode_to_dict(self, topic: TopicMap, raw: bytes) -> dict[str, Any]:
        """Decode LoRa binary bytes into a dictionary of {field_name: value}."""
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

    def _normalize_input_values(self, topic: TopicMap, data: Any) -> dict[str, Any]:
        """Extract a dictionary of {field_name: value} from various input formats."""
        if isinstance(data, dict):
            return data

        # Parse string or bytes input
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
            # Try parsing as JSON first
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
                # If JSON parsed as a scalar (number, bool, string)
                if len(topic.fields) == 1:
                    return {topic.fields[0].name: parsed}
            except (json.JSONDecodeError, ValueError):
                pass

            # If plain text scalar for a single field
            if len(topic.fields) == 1:
                return {topic.fields[0].name: text}

            raise ValueError(
                f"Expected JSON object for multi-field topic '{topic.mqtt_topic}', got: {text!r}"
            )

        # Direct scalar (int, float, bool)
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
        # Clean up floating point representation precision for JSON serialization
        if ftype in ("float", "float32"):
            val = round(val, 4)
        elif ftype in ("double", "float64"):
            val = round(val, 6)

        return val, offset + size
