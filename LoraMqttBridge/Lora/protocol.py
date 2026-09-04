"""LoRa-Frame-Encoding/Decoding (4-Byte-Header + Payload).

Siehe docs/PROTOCOL.md für die Bit-Belegung.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


PROTOCOL_VERSION = 1  # 3-bit Versionsfeld
MAX_PAYLOAD_LEN = 251  # SX1262 max 255 - 4 Byte Header


class FrameType(IntEnum):
    MQTT = 0x01
    CONTROL = 0x02
    ACK = 0x03
    HELLO = 0x04


# Flag-Bits in Byte 0 (0..4). Byte0 = (version<<5) | flags
FLAG_ACK_REQ = 1 << 4
FLAG_ACK_RSP = 1 << 3
FLAG_RETRY = 1 << 2


class AckStatus(IntEnum):
    OK = 0
    BUSY = 1
    ERROR = 2


@dataclass
class Frame:
    version: int
    ftype: FrameType
    seq: int
    topic_id: int
    payload: bytes = b""
    ack_req: bool = False
    ack_rsp: bool = False
    retry: bool = False

    def encode(self) -> bytes:
        if not (0 <= self.version <= 7):
            raise ValueError(f"version out of range: {self.version}")
        if not (0 <= self.seq <= 255):
            raise ValueError(f"seq out of range: {self.seq}")
        if not (0 <= self.topic_id <= 255):
            raise ValueError(f"topic_id out of range: {self.topic_id}")
        if len(self.payload) > MAX_PAYLOAD_LEN:
            raise ValueError(
                f"payload too long: {len(self.payload)} > {MAX_PAYLOAD_LEN}"
            )
        flags = 0
        if self.ack_req:
            flags |= FLAG_ACK_REQ
        if self.ack_rsp:
            flags |= FLAG_ACK_RSP
        if self.retry:
            flags |= FLAG_RETRY
        byte0 = ((self.version & 0x07) << 5) | (flags & 0x1F)
        return bytes([byte0, int(self.ftype), self.seq, self.topic_id]) + self.payload

    @classmethod
    def decode(cls, raw: bytes) -> "Frame":
        if len(raw) < 4:
            raise ValueError(f"frame too short: {len(raw)} bytes")
        byte0 = raw[0]
        version = (byte0 >> 5) & 0x07
        flags = byte0 & 0x1F
        try:
            ftype = FrameType(raw[1])
        except ValueError as exc:
            raise ValueError(f"unknown frame type 0x{raw[1]:02X}") from exc
        return cls(
            version=version,
            ftype=ftype,
            seq=raw[2],
            topic_id=raw[3],
            payload=raw[4:],
            ack_req=bool(flags & FLAG_ACK_REQ),
            ack_rsp=bool(flags & FLAG_ACK_RSP),
            retry=bool(flags & FLAG_RETRY),
        )

    def __repr__(self) -> str:
        flag_parts = []
        if self.ack_req:
            flag_parts.append("ACKREQ")
        if self.ack_rsp:
            flag_parts.append("ACKRSP")
        if self.retry:
            flag_parts.append("RETRY")
        flags = ",".join(flag_parts) or "-"
        return (
            f"Frame(v{self.version} {self.ftype.name} seq={self.seq} "
            f"tid={self.topic_id} flags={flags} plen={len(self.payload)})"
        )


def build_ack(for_frame: Frame, status: AckStatus = AckStatus.OK) -> Frame:
    return Frame(
        version=PROTOCOL_VERSION,
        ftype=FrameType.ACK,
        seq=for_frame.seq,
        topic_id=for_frame.topic_id,
        payload=bytes([int(status)]),
        ack_rsp=True,
    )


def build_mqtt(seq: int, topic_id: int, payload: bytes, ack_req: bool) -> Frame:
    return Frame(
        version=PROTOCOL_VERSION,
        ftype=FrameType.MQTT,
        seq=seq,
        topic_id=topic_id,
        payload=payload,
        ack_req=ack_req,
    )
