import json
import tempfile
from pathlib import Path

import pytest

from uart_fixture_kit import (
    Event,
    Fixture,
    Parser,
    ReplayResult,
    chunk_bytes,
    drop_bytes,
    flip_byte,
    load_fixture,
    replay,
    schedule,
)
from uart_fixture_kit.examples import BinaryParser, JsonLineParser, encode_binary
from uart_fixture_kit.optimized import ChunkedJsonLineParser


class MutableRecordParser(Parser):
    """Reuses a single dict across feed, advance, and finish to test defensive snapshotting."""

    def __init__(self) -> None:
        self.shared: dict[str, object] = {"phase": "init", "count": 0}

    def advance(self, now_ms: int):
        self.shared["phase"] = f"advance@{now_ms}"
        self.shared["count"] = int(self.shared["count"]) + 1
        return [self.shared]

    def feed(self, data: bytes, now_ms: int):
        self.shared["phase"] = f"feed@{now_ms}:{data.decode('ascii')}"
        self.shared["count"] = int(self.shared["count"]) + 1
        return [self.shared]

    def finish(self, now_ms: int):
        self.shared["phase"] = f"finish@{now_ms}"
        self.shared["count"] = int(self.shared["count"]) + 1
        return [self.shared]


def test_replay_defensive_snapshot_across_advance_feed_and_finish() -> None:
    parser = MutableRecordParser()
    fixture = Fixture(
        name="mutable-reuse",
        events=(Event(10, b"A"),),
        end_ms=25,
        expected=[
            {"phase": "advance@10", "count": 1},
            {"phase": "feed@10:A", "count": 2},
            {"phase": "advance@25", "count": 3},
            {"phase": "finish@25", "count": 4},
        ],
    )
    result = replay(fixture, parser)
    assert result.passed is True
    assert result.diff == ""
    result.assert_matches()


def test_replay_result_diff_and_assert_matches_report_mismatch() -> None:
    result = ReplayResult(
        name="mismatch-case",
        records=[{"kind": "message", "seq": 2}],
        expected=[{"kind": "message", "seq": 1}],
    )
    assert result.passed is False
    assert "--- expected" in result.diff
    assert "+++ actual" in result.diff
    with pytest.raises(AssertionError, match="mismatch-case"):
        result.assert_matches()


def test_load_fixture_accepts_spaced_hex_and_rejects_nan_or_bool_version() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        valid_path = Path(tmp_dir) / "spaced.json"
        valid_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "name": "spaced-hex",
                    "events": [{"at_ms": 5, "hex": "7b 22 73 65 71 22 3a 30 7d 0a"}],
                    "end_ms": 10,
                    "expected": [{"kind": "diagnostic", "code": "invalid_record"}],
                }
            ),
            encoding="utf-8",
        )
        loaded = load_fixture(valid_path)
        assert loaded.events[0].data == b'{"seq":0}\n'

        bool_ver_path = Path(tmp_dir) / "bool_ver.json"
        bool_ver_path.write_text(
            '{"version": true, "name": "bad", "events": [], "end_ms": 0, "expected": []}',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="fixture version must be 1"):
            load_fixture(bool_ver_path)

        nan_path = Path(tmp_dir) / "nan.json"
        nan_path.write_text(
            '{"version": 1, "name": "nan", "events": [], "end_ms": 0, "expected": [{"x": NaN}]}',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Non-finite JSON number"):
            load_fixture(nan_path)


def test_binary_parser_wraparound_255_to_0_and_int16_extremes() -> None:
    # 255 -> 0 is a valid uint8 wraparound (distance == 1), NOT a discontinuity!
    stream = (
        encode_binary(255, -32768)
        + encode_binary(0, 32767)
        + encode_binary(0, 32767)  # duplicate_sequence
        + encode_binary(2, 0)  # sequence_discontinuity (0 -> 2)
    )
    events = schedule(stream, [3], start_ms=0, interval_ms=2)
    fixture = Fixture("binary-wrap-extremes", events, end_ms=100, expected=[])
    result = replay(fixture, BinaryParser())
    assert result.records == [
        {"kind": "message", "seq": 255, "value": -327.68, "unit": "C"},
        {"kind": "message", "seq": 0, "value": 327.67, "unit": "C"},
        {"kind": "diagnostic", "code": "duplicate_sequence", "seq": 0},
        {"kind": "diagnostic", "code": "sequence_discontinuity", "previous": 0, "seq": 2},
        {"kind": "message", "seq": 2, "value": 0.0, "unit": "C"},
    ]


@pytest.mark.parametrize("prefix_len", [1, 2, 3, 4, 5, 6])
def test_binary_parser_truncated_frame_lengths_emit_single_truncated_diagnostic(prefix_len: int) -> None:
    frame = encode_binary(7, 2500)
    fixture = Fixture(
        name=f"trunc-{prefix_len}",
        events=(Event(0, frame[:prefix_len]),),
        end_ms=10,
        expected=[{"kind": "diagnostic", "code": "truncated"}],
    )
    replay(fixture, BinaryParser()).assert_matches()


def test_json_baseline_and_chunked_equivalence_across_seq_bool_crlf_and_128b_boundaries() -> None:
    # Exact 128-byte payload before \n vs 129-byte oversize payload, boolean seq/value rejection, CRLF lines
    pad_exact_len = 128 - len('{"seq":0,"value":25.0,"unit":"C","p":""}')
    exact_128 = f'{{"seq":0,"value":25.0,"unit":"C","p":"{"x" * pad_exact_len}"}}\n'.encode("ascii")
    assert len(exact_128) == 129  # 128 bytes + newline

    over_129 = f'{{"seq":1,"value":25.0,"unit":"C","p":"{"x" * (pad_exact_len + 1)}"}}\n'.encode("ascii")
    assert len(over_129) == 130  # 129 bytes + newline -> triggers oversize

    payload = b"".join(
        [
            exact_128,
            over_129,
            b'{"seq":true,"value":21.5,"unit":"C"}\n',
            b'{"seq":2,"value":false,"unit":"C"}\n',
            b'{"seq":-1,"value":21.5,"unit":"C"}\n',
            b'{"seq":256,"value":21.5,"unit":"C"}\n',
            b'{"seq":255,"value":21.5,"unit":"C"}\r\n',
        ]
    )
    for chunk_size in (1, 7, 64, len(payload)):
        events = schedule(payload, [chunk_size], start_ms=5, interval_ms=3)
        fixture = Fixture(f"eq-{chunk_size}", events, end_ms=5000, expected=[])
        base_res = replay(fixture, JsonLineParser(stale_ms=20))
        opt_res = replay(fixture, ChunkedJsonLineParser(stale_ms=20))
        assert base_res.records == opt_res.records
        assert base_res.records == [
            {"kind": "message", "seq": 0, "value": 25.0, "unit": "C"},
            {"kind": "diagnostic", "code": "oversize"},
            {"kind": "diagnostic", "code": "invalid_record"},
            {"kind": "diagnostic", "code": "invalid_record"},
            {"kind": "diagnostic", "code": "invalid_record"},
            {"kind": "diagnostic", "code": "invalid_record"},
            {"kind": "message", "seq": 255, "value": 21.5, "unit": "C"},
        ]


def test_fault_combinators_boundary_guards() -> None:
    assert chunk_bytes(b"abcdef", [2, 3]) == (b"ab", b"cde", b"f")
    with pytest.raises(ValueError):
        chunk_bytes(b"abc", [])
    with pytest.raises(ValueError):
        chunk_bytes(b"abc", [0])
    with pytest.raises(ValueError):
        chunk_bytes(b"abc", [True])
    with pytest.raises(ValueError):
        schedule(b"abc", [1], start_ms=True)
    with pytest.raises(ValueError):
        flip_byte(b"abc", 3, 1)
    with pytest.raises(ValueError):
        flip_byte(b"abc", 0, 0)
    with pytest.raises(ValueError):
        drop_bytes(b"abc", 1, 3)
    with pytest.raises(ValueError):
        drop_bytes(b"abcd", 1, 0)


def test_profile_rejects_identical_output_and_html_paths(tmp_path) -> None:
    from uart_fixture_kit.cli import main

    same = tmp_path / "same-artifact.out"
    rc = main(["profile", "--records", "16", "--repeats", "3", "--warmups", "1", "--output", str(same), "--html", str(same)])
    assert rc == 2
