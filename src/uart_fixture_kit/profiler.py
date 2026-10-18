"""Measured parser comparison on fixed synthetic inputs, gated by exact records."""

import cProfile
from datetime import datetime, timezone
import gc
import hashlib
from importlib import resources
import inspect
import json
import math
import os
from pathlib import Path
import platform
import pstats
import statistics
from time import perf_counter_ns

from .core import Event, Fixture, load_fixture, replay
from .examples import JsonLineParser
from .faults import schedule
from .optimized import ChunkedJsonLineParser


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fixture_digest(fixture: Fixture) -> str:
    return sha(canonical({"name": fixture.name, "events": [
        {"at_ms": event.at_ms, "hex": event.data.hex()} for event in fixture.events
    ], "end_ms": fixture.end_ms, "expected": fixture.expected}))


def build_workloads(records: int = 2048) -> tuple[Fixture, ...]:
    if type(records) is not int or not 1 <= records <= 10000:
        raise ValueError("records must be an integer from 1 to 10000")
    expected = [{"kind": "message", "seq": i % 256,
                 "value": ((i % 7001) - 3500) / 100, "unit": "C"} for i in range(records)]
    stream = b"".join(canonical({k: value[k] for k in ("seq", "value", "unit")}) + b"\n"
                      for value in expected)
    workloads = []
    for name, sizes in (("bulk-4096", [4096]), ("fragmented-1-2-7-3", [1, 2, 7, 3]), ("bytewise", [1])):
        events = schedule(stream, sizes)
        workloads.append(Fixture(name, events, events[-1].at_ms + 1, expected))
    # Explicit fault oracle: a partial record expires, then malformed bytes,
    # an oversized line and a bad unit precede a valid record and EOF fragment.
    good = b'{"seq":7,"value":12.5,"unit":"C"}\n'
    recovery = b'\xff\n' + b'x' * 129 + b'\n' + b'{"seq":1,"value":2,"unit":"mV"}\n' + good
    tail = schedule(recovery, [2, 17, 1], start_ms=51)
    events = (Event(0, b'{"seq":'), Event(50, b'')) + tail + (Event(tail[-1].at_ms + 1, b'{'),)
    expected_faults = [{"kind": "diagnostic", "code": code} for code in
                       ("stale_partial", "invalid_json", "oversize", "invalid_record")]
    expected_faults += [{"kind": "message", "seq": 7, "value": 12.5, "unit": "C"},
                        {"kind": "diagnostic", "code": "truncated"}]
    workloads.append(Fixture("fault-recovery", events, events[-1].at_ms + 1, expected_faults))
    return tuple(workloads)


def bundled_json_fixtures() -> tuple[Fixture, ...]:
    folder = resources.files("uart_fixture_kit").joinpath("fixtures")
    fixtures = []
    for resource in sorted(folder.iterdir(), key=lambda item: item.name):
        if resource.name.startswith("json_") and resource.name.endswith(".json"):
            with resources.as_file(resource) as path:
                fixtures.append(load_fixture(path))
    return tuple(fixtures)


def run_once(fixture: Fixture, factory) -> list[dict]:
    """Time construction, dispatch and emitted records, without JSON comparison."""
    parser = factory()
    records = []
    for event in fixture.events:
        records.extend(parser.advance(event.at_ms))
        records.extend(parser.feed(event.data, event.at_ms))
    records.extend(parser.advance(fixture.end_ms))
    records.extend(parser.finish(fixture.end_ms))
    return records


def validate_parsers(fixtures, factories) -> dict:
    checks = []
    for fixture in fixtures:
        for name, factory in factories.items():
            result = replay(fixture, factory())
            checks.append({"fixture": fixture.name, "parser": name, "passed": result.passed,
                           "diff": result.diff if not result.passed else ""})
    return {"passed": all(item["passed"] for item in checks), "checks": checks}


def _measure(fixture, factory) -> tuple[int, list[dict]]:
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


def summarize(samples: list[int]) -> dict:
    ordered = sorted(samples)
    return {"samples_ns": samples, "median_ns": statistics.median(samples),
            "min_ns": min(samples), "max_ns": max(samples),
            "p95_ns": ordered[math.ceil(0.95 * len(samples)) - 1],
            "population_stdev_ns": statistics.pstdev(samples)}


def hotspots(fixture, factory) -> list[dict]:
    profile = cProfile.Profile()
    records = profile.runcall(run_once, fixture, factory)
    if canonical(records) != canonical(fixture.expected):
        raise ValueError("Profiled run changed parser output")
    stats = pstats.Stats(profile)
    rows = []
    for (filename, line, function), (primitive, calls, total, cumulative, _) in stats.stats.items():
        rows.append({"file": Path(filename).name, "line": line, "function": function,
                     "calls": calls, "primitive_calls": primitive,
                     "self_seconds": total, "cumulative_seconds": cumulative})
    return sorted(rows, key=lambda row: row["cumulative_seconds"], reverse=True)[:15]


def run_profile(records: int = 2048, repeats: int = 7, warmups: int = 2) -> dict:
    if type(repeats) is not int or not 3 <= repeats <= 31:
        raise ValueError("repeats must be an integer from 3 to 31")
    if type(warmups) is not int or not 1 <= warmups <= 10:
        raise ValueError("warmups must be an integer from 1 to 10")
    workloads = build_workloads(records)
    factories = {"bytewise_reference": JsonLineParser, "chunk_scanning": ChunkedJsonLineParser}
    fixtures = bundled_json_fixtures() + workloads
    gate = validate_parsers(fixtures, factories)
    report = {"schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
              "data": "synthetic; no hardware or private captures", "passed": gate["passed"],
              "parameters": {"records": records, "repeats": repeats, "warmups": warmups},
              "environment": {"python": platform.python_version(), "implementation": platform.python_implementation(),
                              "platform": platform.platform(), "machine": platform.machine(),
                              "processor": platform.processor(), "logical_cpus": os.cpu_count()},
              "method": {"timer": "perf_counter_ns", "gc": "disabled only during timed runs, restored afterward",
                         "order": "alternating baseline/candidate order per paired repeat",
                         "scope": "fresh parser construction, virtual-event dispatch, emitted records; validation outside timer",
                         "p95": "nearest-rank observed sample", "speed_ratio": "reference median / chunk-scanning median"},
              "sources": {"reference_sha256": sha(inspect.getsource(JsonLineParser).encode("utf-8")),
                          "candidate_sha256": sha(inspect.getsource(ChunkedJsonLineParser).encode("utf-8"))},
              "fixture_manifest": [{"name": fixture.name, "sha256": fixture_digest(fixture)} for fixture in fixtures],
              "gate": gate, "workloads": [], "hotspots": {}}
    if not gate["passed"]:
        return report
    for fixture in workloads:
        expected = canonical(fixture.expected)
        for _ in range(warmups):
            for factory in factories.values():
                if canonical(run_once(fixture, factory)) != expected:
                    raise ValueError("Warm-up output changed after the correctness gate")
        samples = {name: [] for name in factories}
        for repeat in range(repeats):
            order = list(factories) if repeat % 2 == 0 else list(reversed(factories))
            for name in order:
                elapsed, result = _measure(fixture, factories[name])
                if canonical(result) != expected:
                    raise ValueError("Timed output changed after the correctness gate")
                samples[name].append(elapsed)
        timings = {name: summarize(values) for name, values in samples.items()}
        reference = timings["bytewise_reference"]["median_ns"]
        candidate = timings["chunk_scanning"]["median_ns"]
        report["workloads"].append({"name": fixture.name, "fixture_sha256": fixture_digest(fixture),
            "expected_sha256": sha(expected), "bytes": sum(len(event.data) for event in fixture.events),
            "events": len(fixture.events), "emitted_records": len(fixture.expected), "timings": timings,
            "speed_ratio": reference / candidate if candidate else None,
            "candidate_time_change_percent": (candidate / reference - 1) * 100 if reference else None})
    report["hotspots"] = {name: hotspots(workloads[0], factory) for name, factory in factories.items()}
    return report
