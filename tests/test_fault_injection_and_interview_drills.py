"""Interview-oriented fault drills for an AA 55 + CRC-16-CCITT stream."""

from __future__ import annotations

import time


SYNC = b"\xAA\x55"
CRC_INIT = 0xFFFF
CRC_POLY = 0x1021


def crc16_ccitt(data: bytes, *, init: int = CRC_INIT) -> int:
    """Return CRC-16-CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection."""
    crc = init
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def encode_frame(payload: bytes) -> bytes:
    if not 1 <= len(payload) <= 255:
        raise ValueError("payload must contain 1..255 bytes")
    body = bytes([len(payload)]) + payload
    return SYNC + body + crc16_ccitt(body).to_bytes(2, "big")


class Crc16StreamParser:
    """Small bounded receiver model used to exercise framing and CRC behavior."""

    def __init__(self, *, max_payload: int = 64, max_buffer: int = 256):
        self.buffer = bytearray()
        self.max_payload = max_payload
        self.max_buffer = max_buffer
        self.errors: list[str] = []

    def feed(self, data: bytes) -> list[bytes]:
        frames = []
        for byte in data:
            self.buffer.append(byte)
            self._bound_buffer()
            while True:
                if len(self.buffer) < 2:
                    break
                if self.buffer[:2] != SYNC:
                    del self.buffer[0]
                    continue
                if len(self.buffer) < 3:
                    break
                payload_length = self.buffer[2]
                if not 1 <= payload_length <= self.max_payload:
                    self.errors.append("framing_error")
                    del self.buffer[0]
                    continue
                total = 2 + 1 + payload_length + 2
                if len(self.buffer) < total:
                    break
                candidate = bytes(self.buffer[:total])
                expected = crc16_ccitt(candidate[2:-2])
                actual = int.from_bytes(candidate[-2:], "big")
                if expected != actual:
                    self.errors.append("crc_error")
                    del self.buffer[0]
                    continue
                del self.buffer[:total]
                frames.append(candidate[3:-2])
        return frames

    def _bound_buffer(self) -> None:
        if len(self.buffer) <= self.max_buffer:
            return
        self.errors.append("buffer_overflow")
        tail = self.buffer[-(self.max_buffer - 1):]
        self.buffer[:] = tail if tail else b""


def test_crc16_ccitt_known_vector():
    assert crc16_ccitt(b"123456789") == 0x29B1


def test_partial_starvation_and_one_byte_reassembly():
    payload = b"motor\xaa\x55\x00"
    parser = Crc16StreamParser()

    decoded = []
    for byte in encode_frame(payload):
        decoded.extend(parser.feed(bytes([byte])))

    assert decoded == [payload]
    assert parser.errors == []


def test_multi_chunk_reassembly_when_split_inside_payload():
    payload = b"temperature=23.75"
    frame = encode_frame(payload)
    parser = Crc16StreamParser()

    decoded = []
    for start, end in zip((0, 1, 3, 7, 10), (1, 3, 7, 10, len(frame))):
        decoded.extend(parser.feed(frame[start:end]))

    assert decoded == [payload]


def test_back_to_back_frames_skip_garbage_and_false_sync_in_payload():
    first = b"left\xaa\x55\xff"
    second = b"right\x00"
    stream = encode_frame(first) + b"\xff\x00\xaa" + encode_frame(second)

    parser = Crc16StreamParser()

    assert parser.feed(stream) == [first, second]
    assert parser.errors == []


def test_single_bit_flip_is_detected_by_crc():
    frame = bytearray(encode_frame(b"single-bit fault"))
    frame[5] ^= 0x04

    parser = Crc16StreamParser()

    assert parser.feed(bytes(frame)) == []
    assert parser.errors == ["crc_error"]


def test_double_bit_flip_is_detected_by_crc():
    frame = bytearray(encode_frame(b"double-bit fault"))
    frame[4] ^= 0x01
    frame[9] ^= 0x80

    parser = Crc16StreamParser()

    assert parser.feed(bytes(frame)) == []
    assert parser.errors == ["crc_error"]


def test_buffer_overflow_and_framing_error_recover_to_next_frame():
    parser = Crc16StreamParser(max_buffer=16)
    valid = b"recovered"

    parser.feed(SYNC + bytes([100]))
    parser.feed(SYNC + bytes([32]) + (b"\x00" * 32))
    decoded = parser.feed(encode_frame(valid))

    assert decoded == [valid]
    assert "framing_error" in parser.errors
    assert "buffer_overflow" in parser.errors


def test_high_throughput_10000_frames_are_deterministic():
    payloads = [f"frame-{sequence:05d}".encode() for sequence in range(10_000)]
    stream = b"".join(encode_frame(payload) for payload in payloads)

    timings = []
    for _ in range(2):
        parser = Crc16StreamParser()
        started = time.perf_counter()
        decoded = parser.feed(stream)
        timings.append(time.perf_counter() - started)

        assert decoded == payloads
        assert parser.errors == []

    assert timings[0] < 2.0
    assert timings[1] < 2.0
