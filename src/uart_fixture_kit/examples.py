"""Synthetic reference parsers, not production protocols or hardware drivers."""

from __future__ import annotations

import json
import math
import struct
from typing import Any

from .core import Parser, Record, _milliseconds

MAX_JSON_LINE_BYTES = 128
BINARY_SYNC_FIRST = 0xAA
BINARY_SYNC_SECOND = 0x55
BINARY_PAYLOAD_LENGTH = 3
BINARY_FRAME_LENGTH = 7


def diagnostic(code: str, **fields: Any) -> Record:
    return {"kind": "diagnostic", "code": code, **fields}


def _decode_json_line(line: bytes) -> Record:
    """Parse and validate a single newline-stripped JSON temperature record."""
    try:
        value = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        return diagnostic("invalid_json")
    if (
        not isinstance(value, dict)
        or type(value.get("seq")) is not int
        or not 0 <= value["seq"] <= 255
        or type(value.get("value")) not in (int, float)
        or not math.isfinite(value["value"])
        or value.get("unit") != "C"
    ):
        return diagnostic("invalid_record")
    return {
        "kind": "message",
        "seq": value["seq"],
        "value": value["value"],
        "unit": "C",
    }


class JsonLineParser(Parser):
    """Newline-delimited JSON temperature records with a 128-byte line limit."""

    def __init__(self, stale_ms: int = 50) -> None:
        if type(stale_ms) is not int or stale_ms < 1:
            raise ValueError("stale_ms must be a positive integer")
        self.buffer: bytearray = bytearray()
        self.last_byte_ms: int = 0
        self.discarding: bool = False
        self.stale_ms: int = stale_ms

    def feed(self, data: bytes, now_ms: int) -> list[Record]:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        _milliseconds(now_ms, "now_ms")
        output: list[Record] = []
        for byte in data:
            self.last_byte_ms = now_ms
            if self.discarding:
                if byte == 10:
                    self.discarding = False
                continue
            if byte != 10:
                self.buffer.append(byte)
                if len(self.buffer) > MAX_JSON_LINE_BYTES:
                    self.buffer.clear()
                    self.discarding = True
                    output.append(diagnostic("oversize"))
                continue
            line = bytes(self.buffer)
            self.buffer.clear()
            output.append(_decode_json_line(line))
        return output

    def advance(self, now_ms: int) -> list[Record]:
        _milliseconds(now_ms, "now_ms")
        if (self.buffer or self.discarding) and now_ms - self.last_byte_ms >= self.stale_ms:
            self.buffer.clear()
            self.discarding = False
            return [diagnostic("stale_partial")]
        return []

    def finish(self, now_ms: int) -> list[Record]:
        _milliseconds(now_ms, "now_ms")
        if self.buffer or self.discarding:
            self.buffer.clear()
            self.discarding = False
            return [diagnostic("truncated")]
        return []


def encode_binary(seq: int, centi_celsius: int) -> bytes:
    """AA 55 | payload length=3 | uint8 sequence | int16 LE centi-C | sum8.

    sum8 covers length and payload, modulo 256. This demonstration checksum is
    not a CRC and provides no authenticity or strong corruption guarantee.
    """
    if type(seq) is not int or not 0 <= seq <= 255:
        raise ValueError("seq must be an integer in 0..255")
    if type(centi_celsius) is not int or not -32768 <= centi_celsius <= 32767:
        raise ValueError("centi_celsius must be a signed 16-bit integer (-32768..32767)")
    payload = struct.pack("<Bh", seq, centi_celsius)
    body = bytes([len(payload)]) + payload
    return b"\xaa\x55" + body + bytes([sum(body) & 255])


class BinaryParser(Parser):
    """Bounded synthetic frame parser with checksum, resync and sequence diagnostics."""

    def __init__(self) -> None:
        self.buffer: bytearray = bytearray()
        self.last_seq: int | None = None

    def _record_binary_sample(self, seq: int, centi_celsius: int, output: list[Record]) -> None:
        if self.last_seq is not None:
            distance = (seq - self.last_seq) & 255
            if distance == 0:
                output.append(diagnostic("duplicate_sequence", seq=seq))
                return
            if distance != 1:
                output.append(
                    diagnostic("sequence_discontinuity", previous=self.last_seq, seq=seq)
                )
        self.last_seq = seq
        output.append({"kind": "message", "seq": seq, "value": centi_celsius / 100, "unit": "C"})

    def feed(self, data: bytes, now_ms: int) -> list[Record]:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        _milliseconds(now_ms, "now_ms")
        output: list[Record] = []
        if not data:
            return output
        self.buffer.extend(data)
        buf = self.buffer
        pos = 0
        total = len(buf)
        while pos < total:
            sync_pos = buf.find(BINARY_SYNC_FIRST, pos)
            if sync_pos < 0:
                pos = total
                break
            pos = sync_pos
            if total - pos < 2:
                break
            if buf[pos + 1] != BINARY_SYNC_SECOND:
                pos += 1
                continue
            if total - pos < 3:
                break
            if buf[pos + 2] != BINARY_PAYLOAD_LENGTH:
                output.append(diagnostic("bad_length"))
                pos += 1
                continue
            if total - pos < BINARY_FRAME_LENGTH:
                break
            checksum = (buf[pos + 2] + buf[pos + 3] + buf[pos + 4] + buf[pos + 5]) & 255
            if checksum != buf[pos + 6]:
                output.append(diagnostic("bad_checksum"))
                pos += 1
                continue
            seq, centi_celsius = struct.unpack_from("<Bh", buf, pos + 3)
            pos += BINARY_FRAME_LENGTH
            self._record_binary_sample(seq, centi_celsius, output)
        if pos:
            del buf[:pos]
        return output

    def finish(self, now_ms: int) -> list[Record]:
        _milliseconds(now_ms, "now_ms")
        if self.buffer:
            self.buffer.clear()
            return [diagnostic("truncated")]
        return []
