"""A small, protocol-independent fixture contract."""

from dataclasses import dataclass
import difflib
import json
from pathlib import Path
from typing import Any, Iterable

Record = dict[str, Any]


def _milliseconds(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number: {value}")


@dataclass(frozen=True)
class Event:
    at_ms: int
    data: bytes

    def __post_init__(self) -> None:
        _milliseconds(self.at_ms, "at_ms")
        if not isinstance(self.data, bytes):
            raise ValueError("event data must be bytes")


@dataclass(frozen=True)
class Fixture:
    name: str
    events: tuple[Event, ...]
    end_ms: int
    expected: list[Record]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("name must be a non-empty string")
        _milliseconds(self.end_ms, "end_ms")
        previous = 0
        for event in self.events:
            if event.at_ms < previous or event.at_ms > self.end_ms:
                raise ValueError("events must be ordered and at or before end_ms")
            previous = event.at_ms
        if not isinstance(self.expected, list) or any(
            not isinstance(record, dict) for record in self.expected
        ):
            raise ValueError("expected must be a list of JSON objects")
        json.dumps(self.expected, allow_nan=False)


def load_fixture(path: str | Path) -> Fixture:
    """Load version 1 JSON. Hex is raw input; expected records are parser-defined."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=_reject_constant)
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1:
        raise ValueError("fixture version must be 1")
    try:
        if not isinstance(raw["events"], list):
            raise ValueError("events must be a list")
        events = tuple(Event(item["at_ms"], bytes.fromhex(item["hex"])) for item in raw["events"])
        return Fixture(raw["name"], events, raw["end_ms"], raw["expected"])
    except (KeyError, TypeError) as error:
        raise ValueError(f"Invalid fixture field: {error}") from error


class Parser:
    """Adapter base. Override feed; optionally override advance and finish.

    Every method returns an iterable of JSON object records. Times are virtual
    milliseconds. No serial transport, threading, or sleeping occurs here.
    """

    def feed(self, data: bytes, now_ms: int) -> Iterable[Record]:
        raise NotImplementedError

    def advance(self, now_ms: int) -> Iterable[Record]:
        return ()

    def finish(self, now_ms: int) -> Iterable[Record]:
        return ()


@dataclass(frozen=True)
class ReplayResult:
    name: str
    records: list[Record]
    expected: list[Record]

    @property
    def passed(self) -> bool:
        return json.dumps(self.records, sort_keys=True, allow_nan=False) == json.dumps(
            self.expected, sort_keys=True, allow_nan=False
        )

    @property
    def diff(self) -> str:
        return "".join(difflib.unified_diff(
            json.dumps(self.expected, indent=2, sort_keys=True).splitlines(keepends=True),
            json.dumps(self.records, indent=2, sort_keys=True).splitlines(keepends=True),
            fromfile="expected", tofile="actual",
        ))

    def assert_matches(self) -> None:
        if not self.passed:
            raise AssertionError(f"Fixture {self.name!r} failed\n{self.diff}")


def replay(fixture: Fixture, parser: Parser) -> ReplayResult:
    """Replay an already validated fixture with a fresh parser instance.

    advance runs before each event and at end_ms, then finish runs once. Empty
    events allow explicit timer checks. Records compare exactly and in order.
    Parser exceptions propagate to preserve their original tracebacks.
    """
    records: list[Record] = []

    def collect(output: Iterable[Record]) -> None:
        for record in output:
            if not isinstance(record, dict):
                raise TypeError("parser must emit JSON object records")
            # Snapshot records so a parser reusing mutable objects cannot rewrite history.
            records.append(json.loads(json.dumps(record, allow_nan=False)))

    for event in fixture.events:
        collect(parser.advance(event.at_ms))
        collect(parser.feed(event.data, event.at_ms))
    collect(parser.advance(fixture.end_ms))
    collect(parser.finish(fixture.end_ms))
    return ReplayResult(fixture.name, records, fixture.expected)
