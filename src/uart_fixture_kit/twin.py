"""Synthetic transport faults decoded by the existing binary reference parser."""

from __future__ import annotations

import hashlib
import inspect
import itertools
import json
from collections.abc import Iterable, Mapping, Sequence
from importlib import resources
from typing import Any

from .core import Record
from .examples import BinaryParser, encode_binary


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _validate_scenario_args(
    frames: int,
    *,
    drop: bool,
    corrupt: bool,
    delay: bool,
) -> None:
    if type(frames) is not int or not 12 <= frames <= 240:
        raise ValueError("frames must be an integer from 12 to 240")
    if any(type(flag) is not bool for flag in (drop, corrupt, delay)):
        raise ValueError("fault switches must be booleans")


def _build_transmissions(
    frames: int,
    *,
    drop: bool,
    corrupt: bool,
    delay: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []
    arrivals: list[dict[str, Any]] = []
    injections: list[dict[str, Any]] = []
    for seq in range(frames):
        phase = seq % 40
        centi = 2200 + min(phase, 40 - phase) * 35
        created = seq * 100
        frame = encode_binary(seq, centi)
        sent.append(
            {
                "seq": seq,
                "created_ms": created,
                "value": centi / 100,
                "unit": "C",
                "hex": frame.hex(),
            }
        )
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
            arrivals.append(
                {"at_ms": created + lateness + part, "hex": data.hex(), "source_seq": seq}
            )
    arrivals.sort(key=lambda item: (int(item["at_ms"]), int(item["source_seq"])))
    return sent, arrivals, injections


def _replay_arrivals(
    sent: Sequence[Mapping[str, Any]],
    arrivals: Sequence[Mapping[str, Any]],
    end_ms: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    parser = BinaryParser()
    timeline: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    state: dict[str, Any] = {
        "messages": 0,
        "checksum_errors": 0,
        "sequence_anomalies": 0,
        "diagnostics": 0,
        "late_ignored": 0,
        "last_sample": None,
    }

    def consume(output: Iterable[Record], now: int) -> None:
        for record in output:
            entry: dict[str, Any] = {"received_ms": now, "record": record}
            if record["kind"] == "diagnostic":
                state["diagnostics"] += 1
                state["checksum_errors"] += int(record["code"] == "bad_checksum")
                state["sequence_anomalies"] += int(
                    record["code"] in ("duplicate_sequence", "sequence_discontinuity")
                )
            else:
                seq = int(record["seq"])
                origin = int(sent[seq]["created_ms"])
                state["messages"] += 1
                last_sample = state["last_sample"]
                displayed = last_sample is None or origin >= int(last_sample["origin_ms"])
                entry.update(origin_ms=origin, latency_ms=now - origin, displayed=displayed)
                if displayed:
                    state["last_sample"] = {
                        "seq": seq,
                        "value": record["value"],
                        "unit": "C",
                        "origin_ms": origin,
                        "received_ms": now,
                    }
                else:
                    state["late_ignored"] += 1
            records.append(entry)

    for event in arrivals:
        at_ms = int(event["at_ms"])
        consume(parser.advance(at_ms), at_ms)
        consume(parser.feed(bytes.fromhex(str(event["hex"])), at_ms), at_ms)
        # Values are replaced rather than mutated, so prior snapshots are stable.
        timeline.append({"at_ms": at_ms, **state})
    consume(parser.advance(end_ms), end_ms)
    consume(parser.finish(end_ms), end_ms)
    timeline.append({"at_ms": end_ms, **state})
    return timeline, records


def scenario(
    frames: int = 120,
    *,
    drop: bool = False,
    corrupt: bool = False,
    delay: bool = False,
) -> dict[str, Any]:
    """120 frames by default, generated every 100 virtual ms, never from hardware.

    A sequence fits in one byte and never wraps in this small bank, so origin
    timestamps have an unambiguous mapping. Existing fixtures test wraparound.
    """
    _validate_scenario_args(frames, drop=drop, corrupt=corrupt, delay=delay)
    sent, arrivals, injections = _build_transmissions(
        frames, drop=drop, corrupt=corrupt, delay=delay
    )
    end_ms = (frames - 1) * 100 + 1000
    timeline, records = _replay_arrivals(sent, arrivals, end_ms)
    result: dict[str, Any] = {
        "id": int(drop) + 2 * int(corrupt) + 4 * int(delay),
        "faults": {"drop": drop, "corrupt": corrupt, "delay": delay},
        "frames": frames,
        "end_ms": end_ms,
        "stale_after_ms": 300,
        "unit": "C",
        "data": "synthetic",
        "sent": sent,
        "arrivals": arrivals,
        "injections": injections,
        "timeline": timeline,
        "records": records,
    }
    result["sha256"] = hashlib.sha256(_json(result).encode("utf-8")).hexdigest()
    return result


def build_twin_bank(frames: int = 120) -> dict[str, Any]:
    scenarios = [
        scenario(frames, drop=drop, corrupt=corrupt, delay=delay)
        for drop, corrupt, delay in itertools.product((False, True), repeat=3)
    ]
    scenarios.sort(key=lambda item: int(item["id"]))
    parser_source = inspect.getsource(BinaryParser) + inspect.getsource(encode_binary)
    return {
        "schema_version": 1,
        "data": "synthetic",
        "unit": "C",
        "scenarios": scenarios,
        "protocol": "AA 55 | length=3 | sequence:u8 | centi-C:i16 LE | sum8",
        "parser_sha256": hashlib.sha256(parser_source.encode("utf-8")).hexdigest(),
        "method": {
            "sample_interval_ms": 100,
            "decode_chunk_offsets_ms": [0, 1, 2],
            "drop": "every fifth generated frame, applied first",
            "corrupt": "every seventh generated frame, if retained: noise prefix and flipped checksum byte",
            "delay": "every sixth generated frame, if retained: delayed by 350 virtual ms",
            "freshness": "time since sample generation, stale at >= 300 ms",
            "late_policy": "decode late messages, but never overwrite a newer displayed sample",
            "limits": "synthetic timing and a projected 3D glyph; no device model, hardware or physiological validation",
        },
    }


def render_twin_html(bank: Mapping[str, Any]) -> str:
    assets = resources.files("uart_fixture_kit").joinpath("assets")
    template = assets.joinpath("twin.html").read_text(encoding="utf-8")
    css = assets.joinpath("twin.css").read_text(encoding="utf-8")
    js = assets.joinpath("twin_ui.mjs").read_text(encoding="utf-8")
    data = _json(bank).replace("<", "\\u003c")
    return (
        template.replace("/* TWIN_CSS */", css)
        .replace("/* TWIN_DATA */", data)
        .replace("/* TWIN_JS */", js)
    )
