import gc
import json
import random
import subprocess
import sys

import pytest

from uart_fixture_kit import Event, Fixture, replay, schedule
from uart_fixture_kit.examples import JsonLineParser
from uart_fixture_kit.optimized import ChunkedJsonLineParser
from uart_fixture_kit.profiler import (build_workloads, bundled_json_fixtures,
    canonical, fixture_digest, run_profile, validate_parsers)


@pytest.mark.parametrize("fixture", bundled_json_fixtures(), ids=lambda fixture: fixture.name)
def test_chunk_parser_matches_saved_oracles(fixture):
    replay(fixture, ChunkedJsonLineParser()).assert_matches()


LINE = b'{"seq":1,"value":25.0,"unit":"C"}'


@pytest.mark.parametrize("split", range(len(LINE) + 2))
def test_every_split_preserves_complete_and_empty_lines(split):
    stream = LINE + b"\n\n" + LINE + b"\n"
    expected = [{"kind": "message", "seq": 1, "value": 25.0, "unit": "C"},
                {"kind": "diagnostic", "code": "invalid_json"},
                {"kind": "message", "seq": 1, "value": 25.0, "unit": "C"}]
    fixture = Fixture("split", (Event(0, stream[:split]), Event(1, stream[split:])), 2, expected)
    replay(fixture, ChunkedJsonLineParser()).assert_matches()


@pytest.mark.parametrize("length", [127, 128, 129])
@pytest.mark.parametrize("sizes", [[1], [17, 2, 3], [10000]])
def test_line_limit_and_recovery(length, sizes):
    line = LINE + b" " * (length - len(LINE))
    message = {"kind": "message", "seq": 1, "value": 25.0, "unit": "C"}
    expected = [message if length <= 128 else {"kind": "diagnostic", "code": "oversize"}, message]
    events = schedule(line + b"\n" + LINE + b"\n", sizes)
    fixture = Fixture("limit", events, events[-1].at_ms + 1, expected)
    replay(fixture, ChunkedJsonLineParser()).assert_matches()


def test_huge_partial_is_bounded_and_empty_feed_does_not_reset_timeout():
    parser = ChunkedJsonLineParser()
    assert parser.feed(b"x" * 1000000, 0) == [{"kind": "diagnostic", "code": "oversize"}]
    assert not parser.buffer and parser.discarding
    assert parser.feed(b"", 49) == []
    assert parser.advance(50) == [{"kind": "diagnostic", "code": "stale_partial"}]
    assert not parser.discarding
    assert parser.feed(LINE + b"\n", 51) == [{"kind": "message", "seq": 1, "value": 25.0, "unit": "C"}]


def test_seeded_mixed_stream_matches_reference_for_many_chunk_boundaries():
    rng = random.Random(1751)
    stream = (LINE + b"\n" + b"x" * 129 + b"\n\xff\n{broken}\n\n") * 20 + b'{'
    for _ in range(25):
        events = schedule(stream, [rng.randint(1, 40) for _ in range(7)])
        fixture = Fixture("mixed", events, events[-1].at_ms + 1, [])
        reference = replay(fixture, JsonLineParser()).records
        assert canonical(replay(fixture, ChunkedJsonLineParser()).records) == canonical(reference)


def test_workloads_are_reproducible_and_match_explicit_expected_records():
    first, second = build_workloads(7), build_workloads(7)
    assert [fixture_digest(item) for item in first] == [fixture_digest(item) for item in second]
    assert fixture_digest(first[0]) != fixture_digest(build_workloads(8)[0])
    for fixture in first:
        replay(fixture, JsonLineParser()).assert_matches()
        replay(fixture, ChunkedJsonLineParser()).assert_matches()


def test_broken_candidate_prevents_any_timing(monkeypatch):
    import uart_fixture_kit.profiler as profiler
    class Broken(JsonLineParser):
        def feed(self, data, now_ms):
            return []
    monkeypatch.setattr(profiler, "ChunkedJsonLineParser", Broken)
    monkeypatch.setattr(profiler, "_measure", lambda *args: pytest.fail("Timed a rejected parser"))
    report = run_profile(records=2, repeats=3, warmups=1)
    assert not report["passed"] and not report["workloads"]
    assert any(not check["passed"] for check in report["gate"]["checks"])


def test_small_profile_has_raw_samples_and_restores_gc():
    enabled = gc.isenabled()
    report = run_profile(records=3, repeats=3, warmups=1)
    assert gc.isenabled() == enabled
    assert report["passed"] and len(report["gate"]["checks"]) == 22
    assert len(report["workloads"]) == 4 and len(report["hotspots"]) == 2
    for workload in report["workloads"]:
        assert len(workload["fixture_sha256"]) == 64
        for timing in workload["timings"].values():
            assert len(timing["samples_ns"]) == 3
            assert timing["min_ns"] <= timing["median_ns"] <= timing["p95_ns"] <= timing["max_ns"]


@pytest.mark.parametrize("kwargs", [{"records": True}, {"records": 0}, {"records": 10001},
                                  {"repeats": 2}, {"repeats": True}, {"warmups": 0}])
def test_profile_bounds(kwargs):
    with pytest.raises(ValueError):
        run_profile(**kwargs)


def test_profile_cli_writes_json_and_html(tmp_path):
    target, html = tmp_path / "result.json", tmp_path / "result.html"
    result = subprocess.run([sys.executable, "-m", "uart_fixture_kit", "profile", "--records", "3",
        "--repeats", "3", "--warmups", "1", "--output", str(target), "--html", str(html)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["passed"]
    assert json.loads(target.read_text(encoding="utf-8"))["passed"]
    assert "fault-recovery" in html.read_text(encoding="utf-8")
