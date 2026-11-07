"""Measured parser comparison on fixed synthetic inputs, gated by exact records."""

from __future__ import annotations

import cProfile
import gc
import hashlib
import inspect
import json
import math
import os
import platform
import pstats
import statistics
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from .core import Event, Fixture, Parser, Record, load_fixture, replay
from .examples import JsonLineParser
from .faults import schedule
from .optimized import ChunkedJsonLineParser

ParserFactory = Callable[[], Parser]


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fixture_digest(fixture: Fixture) -> str:
    return sha(
        canonical(
            {
                "name": fixture.name,
                "events": [
                    {"at_ms": event.at_ms, "hex": event.data.hex()} for event in fixture.events
                ],
                "end_ms": fixture.end_ms,
                "expected": fixture.expected,
            }
        )
    )


def _build_fault_recovery_workload() -> Fixture:
    """Explicit fault oracle: expired partial, bad UTF-8, oversize, bad unit, valid, EOF."""
    good = b'{"seq":7,"value":12.5,"unit":"C"}\n'
    recovery = b"\xff\n" + b"x" * 129 + b"\n" + b'{"seq":1,"value":2,"unit":"mV"}\n' + good
    tail = schedule(recovery, [2, 17, 1], start_ms=51)
    events = (Event(0, b'{"seq":'), Event(50, b"")) + tail + (Event(tail[-1].at_ms + 1, b"{"),)
    expected_faults: list[Record] = [
        {"kind": "diagnostic", "code": code}
        for code in ("stale_partial", "invalid_json", "oversize", "invalid_record")
    ]
    expected_faults.extend(
        [
            {"kind": "message", "seq": 7, "value": 12.5, "unit": "C"},
            {"kind": "diagnostic", "code": "truncated"},
        ]
    )
    return Fixture("fault-recovery", events, events[-1].at_ms + 1, expected_faults)


def build_workloads(records: int = 2048) -> tuple[Fixture, ...]:
    if type(records) is not int or not 1 <= records <= 10000:
        raise ValueError("records must be an integer from 1 to 10000")
    expected: list[Record] = [
        {
            "kind": "message",
            "seq": index % 256,
            "value": ((index % 7001) - 3500) / 100,
            "unit": "C",
        }
        for index in range(records)
    ]
    stream = b"".join(
        canonical({key: value[key] for key in ("seq", "value", "unit")}) + b"\n"
        for value in expected
    )
    workloads: list[Fixture] = []
    chunk_profiles: tuple[tuple[str, list[int]], ...] = (
        ("bulk-4096", [4096]),
        ("fragmented-1-2-7-3", [1, 2, 7, 3]),
        ("bytewise", [1]),
    )
    for name, sizes in chunk_profiles:
        events = schedule(stream, sizes)
        workloads.append(Fixture(name, events, events[-1].at_ms + 1, expected))
    workloads.append(_build_fault_recovery_workload())
    return tuple(workloads)


def bundled_json_fixtures() -> tuple[Fixture, ...]:
    folder = resources.files("uart_fixture_kit").joinpath("fixtures")
    fixtures: list[Fixture] = []
    for resource in sorted(folder.iterdir(), key=lambda item: item.name):
        if resource.name.startswith("json_") and resource.name.endswith(".json"):
            with resources.as_file(resource) as path:
                fixtures.append(load_fixture(path))
    return tuple(fixtures)


def run_once(fixture: Fixture, factory: ParserFactory) -> list[Record]:
    """Time construction, dispatch and emitted records, without JSON comparison."""
    parser = factory()
    records: list[Record] = []
    for event in fixture.events:
        records.extend(parser.advance(event.at_ms))
        records.extend(parser.feed(event.data, event.at_ms))
    records.extend(parser.advance(fixture.end_ms))
    records.extend(parser.finish(fixture.end_ms))
    return records


def validate_parsers(
    fixtures: Sequence[Fixture],
    factories: Mapping[str, ParserFactory],
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for fixture in fixtures:
        for name, factory in factories.items():
            result = replay(fixture, factory())
            checks.append(
                {
                    "fixture": fixture.name,
                    "parser": name,
                    "passed": result.passed,
                    "diff": result.diff if not result.passed else "",
                }
            )
    return {"passed": all(item["passed"] for item in checks), "checks": checks}


def _measure(fixture: Fixture, factory: ParserFactory) -> tuple[int, list[Record]]:
    enabled = gc.isenabled()
    gc.disable()
    try:
        started = perf_counter_ns()
        records = run_once(fixture, factory)
        elapsed = perf_counter_ns() - started
    finally:
        if enabled:
            gc.enable()
    return elapsed, records


def summarize(samples: Sequence[int]) -> dict[str, Any]:
    sample_list = list(samples)
    if not sample_list:
        raise ValueError("samples must not be empty")
    ordered = sorted(sample_list)
    return {
        "samples_ns": sample_list,
        "median_ns": statistics.median(sample_list),
        "min_ns": min(sample_list),
        "max_ns": max(sample_list),
        "p95_ns": ordered[math.ceil(0.95 * len(sample_list)) - 1],
        "population_stdev_ns": statistics.pstdev(sample_list),
    }


def hotspots(fixture: Fixture, factory: ParserFactory) -> list[dict[str, Any]]:
    profile = cProfile.Profile()
    records = profile.runcall(run_once, fixture, factory)
    if canonical(records) != canonical(fixture.expected):
        raise ValueError("Profiled run changed parser output")
    stats = pstats.Stats(profile)
    raw_stats: dict[tuple[str, int, str], tuple[int, int, float, float, Any]] = getattr(
        stats, "stats", {}
    )
    rows: list[dict[str, Any]] = []
    for (filename, line, function), (primitive, calls, total, cumulative, _) in raw_stats.items():
        rows.append(
            {
                "file": Path(filename).name,
                "line": line,
                "function": function,
                "calls": calls,
                "primitive_calls": primitive,
                "self_seconds": total,
                "cumulative_seconds": cumulative,
            }
        )
    return sorted(rows, key=lambda row: float(row["cumulative_seconds"]), reverse=True)[:15]


def _validate_profile_args(repeats: int, warmups: int) -> None:
    if type(repeats) is not int or not 3 <= repeats <= 31:
        raise ValueError("repeats must be an integer from 3 to 31")
    if type(warmups) is not int or not 1 <= warmups <= 10:
        raise ValueError("warmups must be an integer from 1 to 10")


def _environment_info() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "logical_cpus": os.cpu_count(),
    }


def _method_info() -> dict[str, str]:
    return {
        "timer": "perf_counter_ns",
        "gc": "disabled only during timed runs, restored afterward",
        "order": "alternating baseline/candidate order per paired repeat",
        "scope": "fresh parser construction, virtual-event dispatch, emitted records; validation outside timer",
        "p95": "nearest-rank observed sample",
        "speed_ratio": "reference median / chunk-scanning median",
    }


def _benchmark_workload(
    fixture: Fixture,
    factories: Mapping[str, ParserFactory],
    *,
    repeats: int,
    warmups: int,
) -> dict[str, Any]:
    expected = canonical(fixture.expected)
    for _ in range(warmups):
        for factory in factories.values():
            if canonical(run_once(fixture, factory)) != expected:
                raise ValueError("Warm-up output changed after the correctness gate")
    samples: dict[str, list[int]] = {name: [] for name in factories}
    factory_names = list(factories)
    reversed_names = list(reversed(factory_names))
    for repeat in range(repeats):
        order = factory_names if repeat % 2 == 0 else reversed_names
        for name in order:
            elapsed, result = _measure(fixture, factories[name])
            if canonical(result) != expected:
                raise ValueError("Timed output changed after the correctness gate")
            samples[name].append(elapsed)
    timings = {name: summarize(values) for name, values in samples.items()}
    reference = float(timings["bytewise_reference"]["median_ns"])
    candidate = float(timings["chunk_scanning"]["median_ns"])
    return {
        "name": fixture.name,
        "fixture_sha256": fixture_digest(fixture),
        "expected_sha256": sha(expected),
        "bytes": sum(len(event.data) for event in fixture.events),
        "events": len(fixture.events),
        "emitted_records": len(fixture.expected),
        "timings": timings,
        "speed_ratio": reference / candidate if candidate else None,
        "candidate_time_change_percent": (candidate / reference - 1) * 100 if reference else None,
    }


def run_profile(records: int = 2048, repeats: int = 7, warmups: int = 2) -> dict[str, Any]:
    _validate_profile_args(repeats, warmups)
    workloads = build_workloads(records)
    factories: dict[str, ParserFactory] = {
        "bytewise_reference": JsonLineParser,
        "chunk_scanning": ChunkedJsonLineParser,
    }
    fixtures = bundled_json_fixtures() + workloads
    gate = validate_parsers(fixtures, factories)
    report: dict[str, Any] = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "data": "synthetic; no hardware or private captures",
        "passed": gate["passed"],
        "parameters": {"records": records, "repeats": repeats, "warmups": warmups},
        "environment": _environment_info(),
        "method": _method_info(),
        "sources": {
            "reference_sha256": sha(inspect.getsource(JsonLineParser).encode("utf-8")),
            "candidate_sha256": sha(inspect.getsource(ChunkedJsonLineParser).encode("utf-8")),
        },
        "fixture_manifest": [
            {"name": fixture.name, "sha256": fixture_digest(fixture)} for fixture in fixtures
        ],
        "gate": gate,
        "workloads": [],
        "hotspots": {},
    }
    if not gate["passed"]:
        return report
    report["workloads"] = [
        _benchmark_workload(fixture, factories, repeats=repeats, warmups=warmups)
        for fixture in workloads
    ]
    report["hotspots"] = {
        name: hotspots(workloads[0], factory) for name, factory in factories.items()
    }
    return report
