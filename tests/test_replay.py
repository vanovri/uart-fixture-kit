import json
from importlib import resources

import pytest

from uart_fixture_kit import Event, Fixture, Parser, load_fixture, replay
from uart_fixture_kit.examples import BinaryParser, JsonLineParser


FIXTURES = sorted(resources.files("uart_fixture_kit").joinpath("fixtures").iterdir(), key=lambda p: p.name)


@pytest.mark.parametrize("resource", FIXTURES, ids=lambda p: p.name)
def test_saved_contract(resource):
    with resources.as_file(resource) as path:
        factory = BinaryParser if path.name.startswith("binary_") else JsonLineParser
        replay(load_fixture(path), factory()).assert_matches()


class Broken(Parser):
    def feed(self, data, now_ms):
        return [{"line": part.decode()} for part in data.splitlines()]


class Buffered(Parser):
    def __init__(self):
        self.pending = bytearray()

    def feed(self, data, now_ms):
        self.pending.extend(data)
        records = []
        while b"\n" in self.pending:
            line, _, remainder = self.pending.partition(b"\n")
            self.pending[:] = remainder
            records.append({"line": line.decode()})
        return records


def test_regression_detects_bug_and_accepts_fix():
    fixture = Fixture("split-line", (Event(0, b"O"), Event(1, b"K\n")), 2, [{"line": "OK"}])
    with pytest.raises(AssertionError, match="split-line"):
        replay(fixture, Broken()).assert_matches()
    replay(fixture, Buffered()).assert_matches()


def test_virtual_order_and_eof_without_sleep():
    calls = []

    class Recorder(Parser):
        def advance(self, now_ms):
            calls.append(("advance", now_ms))
            return []

        def feed(self, data, now_ms):
            calls.append(("feed", now_ms, data))
            return []

        def finish(self, now_ms):
            calls.append(("finish", now_ms))
            return []

    fixture = Fixture("timer", (Event(0, b"a"), Event(0, b"b"), Event(50, b"")), 100, [])
    replay(fixture, Recorder()).assert_matches()
    assert calls == [("advance", 0), ("feed", 0, b"a"), ("advance", 0),
                     ("feed", 0, b"b"), ("advance", 50), ("feed", 50, b""),
                     ("advance", 100), ("finish", 100)]


def test_records_are_snapshots():
    class Reused(Parser):
        def feed(self, data, now_ms):
            record = {"nested": {"value": 0}}
            yield record
            record["nested"]["value"] = 1
            yield record

    fixture = Fixture("snapshots", (Event(0, b"x"),), 1,
                      [{"nested": {"value": 0}}, {"nested": {"value": 1}}])
    replay(fixture, Reused()).assert_matches()


def test_boolean_is_not_numeric_match():
    class WrongType(Parser):
        def feed(self, data, now_ms):
            return [{"value": True}]

    assert not replay(Fixture("type", (Event(0, b"x"),), 0, [{"value": 1}]), WrongType()).passed


@pytest.mark.parametrize("events,end", [((Event(2,b"x"),Event(1,b"y")),3), ((Event(2,b"x"),),1)])
def test_invalid_schedule(events, end):
    with pytest.raises(ValueError, match="ordered"):
        Fixture("bad", events, end, [])


@pytest.mark.parametrize("time", [-1, 1.5, True])
def test_invalid_time(time):
    with pytest.raises(ValueError):
        Event(time, b"x")


@pytest.mark.parametrize("change", [
    {"version": 2}, {"version": True}, {"events": [{"at_ms": 0, "hex": "zz"}]},
    {"expected": [0]}, {"events": "bad"}, {"events": [{}]}, {"end_ms": -1},
])
def test_invalid_json_fixture(tmp_path, change):
    raw = {"version": 1, "name": "bad", "events": [], "end_ms": 0, "expected": []}
    raw.update(change)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        load_fixture(path)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "1e999"])
def test_nonfinite_fixture_rejected(tmp_path, value):
    path = tmp_path / "nonfinite.json"
    path.write_text('{"version":1,"name":"bad","events":[],"end_ms":0,"expected":[{"x":'+value+'}]}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_fixture(path)


def test_parser_error_keeps_traceback():
    class Crashes(Parser):
        def feed(self, data, now_ms):
            raise RuntimeError("parser defect")

    with pytest.raises(RuntimeError, match="parser defect"):
        replay(Fixture("crash", (Event(0,b"x"),), 1, []), Crashes())


@pytest.mark.parametrize("bad", [None, 2, [1], {"value": float("nan")}])
def test_invalid_parser_record_rejected(bad):
    class Invalid(Parser):
        def feed(self, data, now_ms):
            return [bad]

    with pytest.raises((TypeError, ValueError)):
        replay(Fixture("bad-output", (Event(0,b"x"),), 1, []), Invalid())
