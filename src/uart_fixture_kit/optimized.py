"""Chunk-scanning alternative to the unchanged byte-wise JSON reference parser."""

import json
import math

from .examples import JsonLineParser, diagnostic


class ChunkedJsonLineParser(JsonLineParser):
    """Same 128-byte line and inactivity contract, scanning chunks with bytes.find.

    advance() and finish() intentionally use the original implementation.
    This is a synthetic reference example, not a serial transport or standard.
    """

    def feed(self, data: bytes, now_ms: int):
        output = []
        if data:
            self.last_byte_ms = now_ms
        offset = 0
        while offset < len(data):
            newline = data.find(b"\n", offset)
            if self.discarding:
                if newline < 0:
                    break
                self.discarding = False
                offset = newline + 1
                continue
            end = len(data) if newline < 0 else newline
            # Check size before slicing so a huge unfinished line cannot grow
            # the bounded buffer or allocate a second copy of the input.
            if len(self.buffer) + end - offset > 128:
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
