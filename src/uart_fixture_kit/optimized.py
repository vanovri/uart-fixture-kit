"""Chunk-scanning alternatives to the byte-wise reference parsers."""

from __future__ import annotations

from .core import Record, _milliseconds
from .examples import (
     MAX_JSON_LINE_BYTES,
    BinaryParser,
    JsonLineParser,
    _decode_json_line,
    diagnostic,
)

__all__ = ["ChunkedBinaryParser", "ChunkedJsonLineParser"]


class ChunkedJsonLineParser(JsonLineParser):
    """Same 128-byte line and inactivity contract, scanning chunks with bytes.find.

    advance() and finish() intentionally use the original implementation.
    This is a synthetic reference example, not a serial transport or standard.
    """

    def feed(self, data: bytes, now_ms: int) -> list[Record]:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        _milliseconds(now_ms, "now_ms")
        output: list[Record] = []
        if data:
            self.last_byte_ms = now_ms
        offset = 0
        total = len(data)
        while offset < total:
            newline = data.find(b"\n", offset)
            if self.discarding:
                if newline < 0:
                    break
                self.discarding = False
                offset = newline + 1
                continue
            end = total if newline < 0 else newline
            # Check size before slicing so a huge unfinished line cannot grow
            # the bounded buffer or allocate a second copy of the input.
            if len(self.buffer) + end - offset > MAX_JSON_LINE_BYTES:
                self.buffer.clear()
                output.append(diagnostic("oversize"))
                self.discarding = newline < 0
                if newline < 0:
                    break
                offset = newline + 1
                continue
            self.buffer.extend(data[offset:end])
            if newline < 0:
                break
            line = bytes(self.buffer)
            self.buffer.clear()
            offset = newline + 1
            output.append(_decode_json_line(line))
        return output


class ChunkedBinaryParser(BinaryParser):
    """Cursor-scanning binary frame parser preserving exact BinaryParser semantics."""
