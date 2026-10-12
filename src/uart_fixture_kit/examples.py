"""Synthetic reference parsers, not production protocols or hardware drivers."""

import json
import math
import struct

from .core import Parser


def diagnostic(code: str, **fields) -> dict:
    return {"kind": "diagnostic", "code": code, **fields}


class JsonLineParser(Parser):
    """Newline-delimited JSON temperature records with a 128-byte line limit."""

    def __init__(self, stale_ms: int = 50):
        if type(stale_ms) is not int or stale_ms < 1:
            raise ValueError("stale_ms must be a positive integer")
        self.buffer = bytearray()
        self.last_byte_ms = 0
        self.discarding = False
        self.stale_ms = stale_ms

    def feed(self, data: bytes, now_ms: int):
        output = []
        for byte in data:
            self.last_byte_ms = now_ms
            if self.discarding:
                if byte == 10:
                    self.discarding = False
                continue
            if byte != 10:
                self.buffer.append(byte)
                if len(self.buffer) > 128:
                    self.buffer.clear()
                    self.discarding = True
                    output.append(diagnostic("oversize"))
                continue
            line = bytes(self.buffer)
            self.buffer.clear()
            try:
                value = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                output.append(diagnostic("invalid_json"))
                continue
            if (not isinstance(value, dict)
                or type(value.get("seq")) is not int or not 0 <= value["seq"] <= 255
                or type(value.get("value")) not in (int, float)
                or not math.isfinite(value["value"]) or value.get("unit") != "C"):
                output.append(diagnostic("invalid_record"))
                continue
            output.append({"kind": "message", "seq": value["seq"], "value": value["value"], "unit": "C"})
        return output

    def advance(self, now_ms: int):
        if (self.buffer or self.discarding) and now_ms - self.last_byte_ms >= self.stale_ms:
            self.buffer.clear()
            self.discarding = False
            return [diagnostic("stale_partial")]
        return []

    def finish(self, now_ms: int):
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
    payload = struct.pack("<Bh", seq, centi_celsius)
    body = bytes([len(payload)]) + payload
    return b"\xaa\x55" + body + bytes([sum(body) & 255])


class BinaryParser(Parser):
    """Bounded synthetic frame parser with checksum, resync and sequence diagnostics."""

    def __init__(self):
        self.buffer = bytearray()
        self.last_seq = None

    def feed(self, data: bytes, now_ms: int):
        output = []
        for byte in data:
            self.buffer.append(byte)
            while self.buffer:
                if self.buffer[0] != 0xAA:
                    del self.buffer[0]
                    continue
                if len(self.buffer) < 2:
                    break
                if self.buffer[1] != 0x55:
                    del self.buffer[0]
                    continue
                if len(self.buffer) < 3:
                    break
                if self.buffer[2] != 3:
                    output.append(diagnostic("bad_length"))
                    del self.buffer[0]
                    continue
                if len(self.buffer) < 7:
                    break
                frame = bytes(self.buffer[:7])
                if sum(frame[2:6]) & 255 != frame[6]:
                    output.append(diagnostic("bad_checksum"))
                    del self.buffer[0]
                    continue
                del self.buffer[:7]
                seq, value = struct.unpack("<Bh", frame[3:6])
                if self.last_seq is not None:
                    distance = (seq - self.last_seq) & 255
                    if distance == 0:
                        output.append(diagnostic("duplicate_sequence", seq=seq))
                        continue
                    if distance != 1:
                        output.append(diagnostic("sequence_discontinuity", previous=self.last_seq, seq=seq))
                self.last_seq = seq
                output.append({"kind": "message", "seq": seq, "value": value / 100, "unit": "C"})
        return output

    def finish(self, now_ms: int):
        if self.buffer:
            self.buffer.clear()
            return [diagnostic("truncated")]
        return []
