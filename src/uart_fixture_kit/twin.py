"""Synthetic transport faults decoded by the existing binary reference parser."""

import hashlib
from importlib import resources
import inspect
import itertools
import json

from .examples import BinaryParser, encode_binary


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def scenario(frames: int = 120, *, drop: bool = False, corrupt: bool = False, delay: bool = False) -> dict:
    """120 frames by default, generated every 100 virtual ms, never from hardware.

    A sequence fits in one byte and never wraps in this small bank, so origin
    timestamps have an unambiguous mapping. Existing fixtures test wraparound.
    """
    if type(frames) is not int or not 12 <= frames <= 240:
        raise ValueError("frames must be an integer from 12 to 240")
    if any(type(flag) is not bool for flag in (drop, corrupt, delay)):
        raise ValueError("fault switches must be booleans")
    sent, arrivals, injections = [], [], []
    for seq in range(frames):
        phase = seq % 40
        centi = 2200 + min(phase, 40 - phase) * 35
        created = seq * 100
        frame = encode_binary(seq, centi)
        sent.append({"seq": seq, "created_ms": created, "value": centi / 100,
                     "unit": "C", "hex": frame.hex()})
        if drop and seq % 5 == 4:
            injections.append({"kind": "drop", "seq": seq, "at_ms": created})
            continue
        if corrupt and seq % 7 == 6:
            frame = b"\x00\xff\x13" + frame[:-1] + bytes([frame[-1] ^ 1])
            injections.append({"kind": "corrupt", "seq": seq, "at_ms": created})
        lateness = 350 if delay and seq % 6 == 5 else 0
        if lateness:
            injections.append({"kind": "delay", "seq": seq, "at_ms": created})
        # Each retained frame arrives in three reads. Delay may reorder frames
        # but cannot interleave their chunks with another frame in this bank.
        parts = (frame[:1], frame[1:3], frame[3:])
        for part, data in enumerate(parts):
            arrivals.append({"at_ms": created + lateness + part, "hex": data.hex(),
                             "source_seq": seq})
    arrivals.sort(key=lambda item: (item["at_ms"], item["source_seq"]))
    end_ms = (frames - 1) * 100 + 1000
    parser = BinaryParser()
    timeline, records = [], []
    state = {"messages": 0, "checksum_errors": 0, "sequence_anomalies": 0,
             "diagnostics": 0, "late_ignored": 0, "last_sample": None}

    def consume(output, now):
        for record in output:
            entry = {"received_ms": now, "record": record}
            if record["kind"] == "diagnostic":
                state["diagnostics"] += 1
                state["checksum_errors"] += record["code"] == "bad_checksum"
                state["sequence_anomalies"] += record["code"] in ("duplicate_sequence", "sequence_discontinuity")
            else:
                origin = sent[record["seq"]]["created_ms"]
                state["messages"] += 1
                displayed = state["last_sample"] is None or origin >= state["last_sample"]["origin_ms"]
                entry.update(origin_ms=origin, latency_ms=now - origin, displayed=displayed)
                if displayed:
                    state["last_sample"] = {"seq": record["seq"], "value": record["value"],
                                            "unit": "C", "origin_ms": origin, "received_ms": now}
                else:
                    state["late_ignored"] += 1
            records.append(entry)

    for event in arrivals:
        consume(parser.advance(event["at_ms"]), event["at_ms"])
        consume(parser.feed(bytes.fromhex(event["hex"]), event["at_ms"]), event["at_ms"])
        # Values are replaced rather than mutated, so prior snapshots are stable.
        timeline.append({"at_ms": event["at_ms"], **state})
    consume(parser.advance(end_ms), end_ms)
    consume(parser.finish(end_ms), end_ms)
    timeline.append({"at_ms": end_ms, **state})
    result = {"id": int(drop) + 2 * int(corrupt) + 4 * int(delay),
              "faults": {"drop": drop, "corrupt": corrupt, "delay": delay},
              "frames": frames, "end_ms": end_ms, "stale_after_ms": 300,
              "unit": "C", "data": "synthetic", "sent": sent, "arrivals": arrivals,
              "injections": injections, "timeline": timeline, "records": records}
    result["sha256"] = hashlib.sha256(_json(result).encode("utf-8")).hexdigest()
    return result


def build_twin_bank(frames: int = 120) -> dict:
    scenarios = [scenario(frames, drop=drop, corrupt=corrupt, delay=delay)
                 for drop, corrupt, delay in itertools.product((False, True), repeat=3)]
    scenarios.sort(key=lambda item: item["id"])
    return {"schema_version": 1, "data": "synthetic", "unit": "C", "scenarios": scenarios,
            "protocol": "AA 55 | length=3 | sequence:u8 | centi-C:i16 LE | sum8",
            "parser_sha256": hashlib.sha256((inspect.getsource(BinaryParser) + inspect.getsource(encode_binary)).encode("utf-8")).hexdigest(),
            "method": {"sample_interval_ms": 100, "decode_chunk_offsets_ms": [0, 1, 2],
                       "drop": "every fifth generated frame, applied first",
                       "corrupt": "every seventh generated frame, if retained: noise prefix and flipped checksum byte",
                       "delay": "every sixth generated frame, if retained: delayed by 350 virtual ms",
                       "freshness": "time since sample generation, stale at >= 300 ms",
                       "late_policy": "decode late messages, but never overwrite a newer displayed sample",
                       "limits": "synthetic timing and a projected 3D glyph; no device model, hardware or physiological validation"}}


def render_twin_html(bank: dict) -> str:
    assets = resources.files("uart_fixture_kit").joinpath("assets")
    template = assets.joinpath("twin.html").read_text(encoding="utf-8")
    data = _json(bank).replace("<", "\\u003c")
    return (template.replace("/* TWIN_CSS */", assets.joinpath("twin.css").read_text(encoding="utf-8"))
            .replace("/* TWIN_DATA */", data)
            .replace("/* TWIN_JS */", assets.joinpath("twin_ui.mjs").read_text(encoding="utf-8")))
