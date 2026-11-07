"""Comprehensive offline unit and characterization tests across uart_fixture_kit."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest

from uart_fixture_kit import (
    Event,
    Fixture,
    Parser,
    chunk_bytes,
    drop_bytes,
    flip_byte,
    load_fixture,
    profiler,
    replay,
    schedule,
)
from uart_fixture_kit.cli import main as cli_main
from uart_fixture_kit.examples import (
    BinaryParser,
    JsonLineParser,
    diagnostic,
    encode_binary,
)
from uart_fixture_kit.optimized import ChunkedJsonLineParser
from uart_fixture_kit.profile_report import render_profile_html

# ---------------------------------------------------------------------------
# 1. Core Contracts & Defensive Snapshotting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_data", ["abc", bytearray(b"abc"), memoryview(b"abc"), None, 123, [1]])
def test_event_rejects_non_bytes_data(bad_data: Any) -> None:
    with pytest.raises(ValueError, match="event data must be bytes"):
        Event(0, bad_data)


@pytest.mark.parametrize("bad_name", ["", None, 123, True, ["name"]])
def test_fixture_rejects_empty_or_non_string_name(bad_name: Any) -> None:
    with pytest.raises(ValueError, match="name must be a non-empty string"):
        Fixture(bad_name, (), 0, [])


@pytest.mark.parametrize(
    "bad_expected",
    [
        "not-a-list",
        ({"a": 1},),
        [None],
        [{"val": float("nan")}],
        [{"val": float("inf")}],
        [{"val": float("-inf")}],
    ],
)
def test_fixture_rejects_invalid_expected_records(bad_expected: Any) -> None:
    with pytest.raises((ValueError, TypeError)):
        Fixture("invalid-expected", (), 0, bad_expected)


def test_load_fixture_edge_cases_and_missing_fields(tmp_path: Path) -> None:
    non_dict = tmp_path / "list.json"
    non_dict.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="fixture version must be 1"):
        load_fixture(str(non_dict))

    missing_key = tmp_path / "missing.json"
    missing_key.write_text('{"version": 1, "name": "x", "events": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid fixture field"):
        load_fixture(missing_key)

    neg_inf = tmp_path / "neginf.json"
    neg_inf.write_text(
        '{"version": 1, "name": "x", "events": [], "end_ms": 0, "expected": [{"v": -Infinity}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Non-finite JSON number"):
        load_fixture(neg_inf)

    with pytest.raises(OSError):
        load_fixture(tmp_path / "does_not_exist.json")


def test_parser_base_contract_methods() -> None:
    base = Parser()
    assert list(base.advance(0)) == []
    assert list(base.finish(10)) == []
    with pytest.raises(NotImplementedError):
        base.feed(b"abc", 0)


def test_replay_nested_snapshot_isolation_and_tuple_conversion() -> None:
    nested_list = [1, 2]
    nested_dict = {"inner": nested_list, "pair": (3, 4), "flag": False, "empty": None}

    class MutatingNestedParser(Parser):
        def feed(self, data: bytes, now_ms: int):
            yield {"payload": nested_dict}
            nested_list.append(999)
            nested_dict["flag"] = True

    fixture = Fixture(
        "nested-snapshot",
        (Event(0, b"x"),),
        5,
        [{"payload": {"inner": [1, 2], "pair": [3, 4], "flag": False, "empty": None}}],
    )
    result = replay(fixture, MutatingNestedParser())
    assert result.passed is True
    assert result.records == [
        {"payload": {"inner": [1, 2], "pair": [3, 4], "flag": False, "empty": None}}
    ]


@pytest.mark.parametrize(
    "bad_record",
    [
        {"nested": [float("inf")]},
        {"nested": {"x": float("-inf")}},
        {"unserializable": object()},
    ],
)
def test_replay_rejects_nested_nonfinite_or_unserializable_values(bad_record: dict[str, Any]) -> None:
    class BadNestedParser(Parser):
        def feed(self, data: bytes, now_ms: int):
            return [bad_record]

    with pytest.raises((TypeError, ValueError)):
        replay(Fixture("bad-nested", (Event(0, b"a"),), 1, []), BadNestedParser())


# ---------------------------------------------------------------------------
# 2. Fault Combinators & Strict Input Guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_bytes", ["abc", bytearray(b"abc"), None, 42, [1, 2]])
def test_faults_reject_non_bytes_inputs(bad_bytes: Any) -> None:
    with pytest.raises(TypeError, match="data must be bytes"):
        chunk_bytes(bad_bytes, [1])
    with pytest.raises(TypeError, match="data must be bytes"):
        schedule(bad_bytes, [1])
    with pytest.raises(TypeError, match="data must be bytes"):
        flip_byte(bad_bytes, 0, 1)
    with pytest.raises(TypeError, match="data must be bytes"):
        drop_bytes(bad_bytes, 0, 1)


def test_faults_zero_interval_and_full_drop_boundaries() -> None:
    events = schedule(b"abcd", [2], start_ms=5, interval_ms=0)
    assert events == (Event(5, b"ab"), Event(5, b"cd"))
    assert schedule(b"", [3], start_ms=10, interval_ms=5) == ()
    assert drop_bytes(b"xyz", 0, 3) == b""
    assert flip_byte(b"\x00\xff", 0, 255) == b"\xff\xff"
    with pytest.raises(ValueError):
        schedule(b"abc", [1], interval_ms=-1)
    with pytest.raises(ValueError):
        schedule(b"abc", [1], interval_ms=True)
    with pytest.raises(ValueError):
        flip_byte(b"abc", 0, True)
    with pytest.raises(ValueError):
        drop_bytes(b"abc", 0, True)


# ---------------------------------------------------------------------------
# 3. Reference & Optimized Parsers + encode_binary Domain Guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_stale", [0, -1, True, False, 1.5, "50", None])
def test_json_parsers_reject_invalid_stale_ms(bad_stale: Any) -> None:
    with pytest.raises(ValueError, match="stale_ms must be a positive integer"):
        JsonLineParser(stale_ms=bad_stale)
    with pytest.raises(ValueError, match="stale_ms must be a positive integer"):
        ChunkedJsonLineParser(stale_ms=bad_stale)


def test_chunked_json_parser_multi_chunk_discarding_without_newline() -> None:
    ref = JsonLineParser(stale_ms=50)
    opt = ChunkedJsonLineParser(stale_ms=50)
    valid_line = b'{"seq":9,"value":20.25,"unit":"C"}\n'
    chunks = [b"a" * 130, b"b" * 200, b"c" * 50 + b"\n" + valid_line]

    ref_out: list[dict[str, Any]] = []
    opt_out: list[dict[str, Any]] = []
    for idx, chunk in enumerate(chunks):
        ref_out.extend(ref.feed(chunk, idx * 10))
        opt_out.extend(opt.feed(chunk, idx * 10))

    expected = [
        diagnostic("oversize"),
        {"kind": "message", "seq": 9, "value": 20.25, "unit": "C"},
    ]
    assert ref_out == expected
    assert opt_out == expected


@pytest.mark.parametrize(
    "seq,centi",
    [
        (True, 0),
        (False, 100),
        (-1, 0),
        (256, 0),
        (1.0, 0),
        (None, 0),
        (0, True),
        (0, False),
        (0, -32769),
        (0, 32768),
        (0, 12.5),
        (0, None),
    ],
)
def test_encode_binary_rejects_bool_and_out_of_range_values(seq: Any, centi: Any) -> None:
    with pytest.raises(ValueError):
        encode_binary(seq, centi)


def test_binary_parser_false_header_bad_length_and_large_noise_resync() -> None:
    # Covers 0xAA followed by non-0x55 (including 0xAA 0xAA 0x55), bad_length != 3, and 32 KB noise
    valid_frame = encode_binary(4, -500)
    bad_len_frame = b"\xaa\x55\x04\x01\x02\x03\x04"
    false_aa = b"\xaa\x00\xaa" + valid_frame
    noisy_stream = (b"\x7f" * 32768) + bad_len_frame + false_aa

    for chunk_size in (1, 5, 128, len(noisy_stream)):
        events = schedule(noisy_stream, [chunk_size], start_ms=0, interval_ms=1)
        fixture = Fixture(
            f"resync-{chunk_size}",
            events,
            events[-1].at_ms + 1,
            [
                diagnostic("bad_length"),
                {"kind": "message", "seq": 4, "value": -5.0, "unit": "C"},
            ],
        )
        replay(fixture, BinaryParser()).assert_matches()


# ---------------------------------------------------------------------------
# 4. Profiler & Profile HTML Report
# ---------------------------------------------------------------------------


def test_render_profile_html_handles_null_ratio_withheld_gate_and_html_escaping() -> None:
    report = profiler.run_profile(records=4, repeats=3, warmups=1)
    html = render_profile_html(report)
    assert "<!doctype html>" in html
    assert "22 / 22 correctness checks passed" in html
    assert "bulk-4096" in html
    assert report["sources"]["reference_sha256"] in html

    # Verify speed_ratio=None -> N/A, gate failure banner, and XSS escaping
    custom_report = json.loads(json.dumps(report))
    custom_report["passed"] = False
    custom_report["workloads"][0]["name"] = "<script>alert(1)</script>"
    custom_report["workloads"][0]["speed_ratio"] = None
    escaped_html = render_profile_html(custom_report)
    assert "<script>alert(1)</script>" not in escaped_html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in escaped_html
    assert "passed; timing withheld" in escaped_html
    assert "<td>N/A</td>" in escaped_html


def test_profiler_zero_division_and_nondeterminism_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError):
        profiler.summarize([])
    with pytest.raises(ValueError):
        profiler.run_profile(records=2, repeats=3, warmups=True)
    with pytest.raises(ValueError):
        profiler.run_profile(records=2, repeats=3, warmups=11)

    # Zero elapsed ns should produce None ratios rather than ZeroDivisionError
    def zero_measure(fixture: Fixture, factory: Any) -> tuple[int, list[dict[str, Any]]]:
        return 0, profiler.run_once(fixture, factory)

    monkeypatch.setattr(profiler, "_measure", zero_measure)
    zero_report = profiler.run_profile(records=2, repeats=3, warmups=1)
    assert zero_report["workloads"][0]["speed_ratio"] is None
    assert zero_report["workloads"][0]["candidate_time_change_percent"] is None

    # Hotspots output drift guard
    fixture = profiler.build_workloads(2)[0]
    with pytest.raises(ValueError, match="Profiled run changed parser output"):
        profiler.hotspots(fixture, lambda: BrokenEmptyParser())

    # Warm-up drift guard: passes gate (first 11 calls), then fails on warm-up (12th call)
    calls = {"count": 0}

    class FlakyWarmupParser(ChunkedJsonLineParser):
        def feed(self, data: bytes, now_ms: int):
            if calls["count"] >= 11:
                return []
            return super().feed(data, now_ms)

        def finish(self, now_ms: int):
            calls["count"] += 1
            return super().finish(now_ms)

    monkeypatch.setattr(profiler, "ChunkedJsonLineParser", FlakyWarmupParser)
    with pytest.raises(ValueError, match="Warm-up output changed"):
        profiler.run_profile(records=2, repeats=3, warmups=1)

    # Timed-run drift guard: passes gate (11) + warm-up (1), then drifts on timed run (13th)
    calls["count"] = 0

    class FlakyTimedParser(ChunkedJsonLineParser):
        def feed(self, data: bytes, now_ms: int):
            if calls["count"] >= 12:
                return []
            return super().feed(data, now_ms)

        def finish(self, now_ms: int):
            calls["count"] += 1
            return super().finish(now_ms)

    def real_or_drifted_measure(fix: Fixture, fac: Any) -> tuple[int, list[dict[str, Any]]]:
        return 100, profiler.run_once(fix, fac)

    monkeypatch.setattr(profiler, "ChunkedJsonLineParser", FlakyTimedParser)
    monkeypatch.setattr(profiler, "_measure", real_or_drifted_measure)
    with pytest.raises(ValueError, match="Timed output changed"):
        profiler.run_profile(records=2, repeats=3, warmups=1)


class BrokenEmptyParser(JsonLineParser):
    def feed(self, data: bytes, now_ms: int):
        return []


# ---------------------------------------------------------------------------
# 5. CLI Subcommands (In-Process Offline) & __main__ Entrypoint
# ---------------------------------------------------------------------------


def test_cli_demo_and_run_human_and_json_modes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Human-readable demo
    assert cli_main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "PASS " in out
    assert "15/15 fixtures passed" in out

    # Passing and failing human-readable `run`
    pass_fixture = tmp_path / "pass.json"
    pass_fixture.write_text(
        json.dumps({"version": 1, "name": "ok-case", "events": [], "end_ms": 0, "expected": []}),
        encoding="utf-8",
    )
    assert cli_main(["run", str(pass_fixture), "--parser", "uart_fixture_kit.examples:JsonLineParser"]) == 0
    assert "PASS ok-case" in capsys.readouterr().out

    fail_fixture = tmp_path / "fail.json"
    fail_fixture.write_text(
        json.dumps(
            {
                "version": 1,
                "name": "mismatch-case",
                "events": [],
                "end_ms": 0,
                "expected": [{"kind": "message", "seq": 1, "value": 1.0, "unit": "C"}],
            }
        ),
        encoding="utf-8",
    )
    assert cli_main(["run", str(fail_fixture), "--parser", "uart_fixture_kit.examples:JsonLineParser"]) == 1
    fail_out = capsys.readouterr().out
    assert "FAIL mismatch-case" in fail_out
    assert "--- expected" in fail_out

    # Invalid parser module / attribute errors in JSON and human modes
    assert cli_main(["run", str(pass_fixture), "--parser", "uart_fixture_kit.examples:NoSuchParser", "--json"]) == 2
    err_json = json.loads(capsys.readouterr().out)
    assert err_json["passed"] is False

    assert cli_main(["run", str(pass_fixture), "--parser", "no_such_module_xyz:Parser"]) == 2
    assert "ERROR:" in capsys.readouterr().err


def test_cli_twin_and_profile_in_process_and_gate_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    twin_html = tmp_path / "twin.html"
    twin_json = tmp_path / "twin.json"
    assert cli_main(["twin", "--frames", "12", "--output", str(twin_html), "--json-output", str(twin_json)]) == 0
    twin_summary = json.loads(capsys.readouterr().out)
    assert twin_summary["scenarios"] == 8
    assert twin_html.exists() and twin_json.exists()

    # Identical paths rejected
    assert cli_main(["twin", "--frames", "12", "--output", str(twin_html), "--json-output", str(twin_html)]) == 2
    capsys.readouterr()

    # Profile in-process success + HTML output
    prof_json = tmp_path / "prof.json"
    prof_html = tmp_path / "prof.html"
    assert (
        cli_main(
            [
                "profile",
                "--records",
                "2",
                "--repeats",
                "3",
                "--warmups",
                "1",
                "--output",
                str(prof_json),
                "--html",
                str(prof_html),
            ]
        )
        == 0
    )
    prof_summary = json.loads(capsys.readouterr().out)
    assert prof_summary["passed"] is True
    assert prof_json.exists() and prof_html.exists()

    # Profile gate failure returns exit code 1
    monkeypatch.setattr(profiler, "ChunkedJsonLineParser", BrokenEmptyParser)
    assert (
        cli_main(
            [
                "profile",
                "--records",
                "2",
                "--repeats",
                "3",
                "--warmups",
                "1",
                "--output",
                str(tmp_path / "failed-prof.json"),
            ]
        )
        == 1
    )
    failed_summary = json.loads(capsys.readouterr().out)
    assert failed_summary["passed"] is False


def test_dunder_main_entrypoint_executes_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["uart-fixture-kit", "demo", "--json"])
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("uart_fixture_kit", run_name="__main__")
    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True


# ---------------------------------------------------------------------------
# 6. Modular Helper & Strict Contract Guards (Step 3 Additions)
# ---------------------------------------------------------------------------


def test_chunked_binary_parser_matches_binary_parser_across_all_splits() -> None:
    from uart_fixture_kit.optimized import ChunkedBinaryParser

    stream = (
        b"\xaa\x01"
        + b"\xaa\x55\x09"
        + encode_binary(0, -32768)
        + flip_byte(encode_binary(1, 0), 6)
        + encode_binary(1, 32767)
        + encode_binary(1, 32767)
        + encode_binary(3, 1250)
    )
    for chunk_size in (1, 2, 3, 7, 64):
        events = schedule(stream, [chunk_size], start_ms=0, interval_ms=1)
        fixture = Fixture(f"chunked-bin-{chunk_size}", events, events[-1].at_ms + 1, [])
        ref = replay(fixture, BinaryParser()).records
        opt = replay(fixture, ChunkedBinaryParser()).records
        assert opt == ref


def test_strict_type_guards_on_replay_fixture_and_parsers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from uart_fixture_kit.optimized import ChunkedBinaryParser

    with pytest.raises(ValueError, match="events must be a tuple"):
        Fixture("bad-events-type", [Event(0, b"a")], 1, [])  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="fixture must be a Fixture"):
        replay("not-a-fixture", JsonLineParser())  # type: ignore[arg-type]

    valid_fixture = Fixture("valid", (), 0, [])
    with pytest.raises(TypeError, match="parser must be a Parser"):
        replay(valid_fixture, object())  # type: ignore[arg-type]

    for parser_instance in (JsonLineParser(), ChunkedJsonLineParser(), BinaryParser(), ChunkedBinaryParser()):
        with pytest.raises(TypeError, match="data must be bytes"):
            parser_instance.feed("abc", 0)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="now_ms must be a non-negative integer"):
            parser_instance.feed(b"abc", -1)
        with pytest.raises(ValueError, match="now_ms must be a non-negative integer"):
            parser_instance.finish(True)  # type: ignore[arg-type]

    # Non-callable --parser target (`__version__` string attribute) returns exit code 2
    fix_path = tmp_path / "f.json"
    fix_path.write_text(
        json.dumps({"version": 1, "name": "f", "events": [], "end_ms": 0, "expected": []}),
        encoding="utf-8",
    )
    assert cli_main(["run", str(fix_path), "--parser", "uart_fixture_kit:__version__", "--json"]) == 2
    err = json.loads(capsys.readouterr().out)
    assert "not callable" in err["error"]
