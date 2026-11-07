"""A small, protocol-independent fixture contract."""

from __future__ import annotations

import difflib
import json
import logging
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

logger = logging.getLogger(__name__)

Record = dict[str, Any]


def _milliseconds(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(f"Non-finite JSON number: {value}")


def _snapshot_json_value(value: Any) -> Any:
    """Create an isolated JSON-compliant deep copy without redundant text parsing."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"Out of range float values are not JSON compliant: {value!r}")
        return value
    if type(value) is list:
        return [_snapshot_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_snapshot_json_value(item) for item in value]
    if type(value) is dict and all(type(key) is str for key in value):
        return {key: _snapshot_json_value(item) for key, item in value.items()}
    return json.loads(json.dumps(value, allow_nan=False))


def _canonical_json(records: Sequence[Record], *, indent: int | None = None) -> str:
    return json.dumps(list(records), indent=indent, sort_keys=True, allow_nan=False)


@dataclass(frozen=True)
class Event:
    at_ms: int
    data: bytes

    def __post_init__(self) -> None:
        _milliseconds(self.at_ms, "at_ms")
        if not isinstance(self.data, bytes):
            raise ValueError("event data must be bytes")  # noqa: TRY004


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
        if not isinstance(self.events, tuple) or any(
            not isinstance(event, Event) for event in self.events
        ):
            raise ValueError("events must be a tuple of Event instances")
        previous = 0
        for event in self.events:
            if event.at_ms < previous or event.at_ms > self.end_ms:
                raise ValueError("events must be ordered and at or before end_ms")
            previous = event.at_ms
        if not isinstance(self.expected, list) or any(
            not isinstance(record, dict) for record in self.expected
        ):
            raise ValueError("expected must be a list of JSON objects")
        _snapshot_json_value(self.expected)


def _parse_fixture_payload(raw: object) -> Fixture:
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1:
        raise ValueError("fixture version must be 1")
    try:
        raw_events = raw["events"]
        if not isinstance(raw_events, list):
            raise TypeError("events must be a list")
        events = tuple(
            Event(item["at_ms"], bytes.fromhex(item["hex"])) for item in raw_events
        )
        return Fixture(raw["name"], events, raw["end_ms"], raw["expected"])
    except (KeyError, TypeError) as error:
        logger.debug("Invalid fixture schema field: %s", error)
        raise ValueError(f"Invalid fixture field: {error}") from error


def load_fixture(path: str | Path) -> Fixture:
    """Load version 1 JSON. Hex is raw input; expected records are parser-defined."""
    fixture_path = Path(path)
    with fixture_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle, parse_constant=_reject_constant)
    return _parse_fixture_payload(raw)


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
        return _canonical_json(self.records) == _canonical_json(self.expected)

    @property
    def diff(self) -> str:
        return "".join(
            difflib.unified_diff(
                _canonical_json(self.expected, indent=2).splitlines(keepends=True),
                _canonical_json(self.records, indent=2).splitlines(keepends=True),
                fromfile="expected",
                tofile="actual",
            )
        )

    def assert_matches(self) -> None:
        if not self.passed:
            raise AssertionError(f"Fixture {self.name!r} failed\n{self.diff}")


def replay(fixture: Fixture, parser: Parser) -> ReplayResult:
    """Replay an already validated fixture with a fresh parser instance.

    advance runs before each event and at end_ms, then finish runs once. Empty
    events allow explicit timer checks. Records compare exactly and in order.
    Parser exceptions propagate to preserve their original tracebacks.
    """
    if not isinstance(fixture, Fixture):
        raise TypeError("fixture must be a Fixture instance")
    if not isinstance(parser, Parser):
        raise TypeError("parser must be a Parser instance")

    records: list[Record] = []

    def collect(output: Iterable[Record]) -> None:
        for record in output:
            if not isinstance(record, dict):
                raise TypeError("parser must emit JSON object records")
            # Snapshot records so a parser reusing mutable objects cannot rewrite history.
            records.append(_snapshot_json_value(record))

    for event in fixture.events:
        collect(parser.advance(event.at_ms))
        collect(parser.feed(event.data, event.at_ms))
    collect(parser.advance(fixture.end_ms))
    collect(parser.finish(fixture.end_ms))
    return ReplayResult(fixture.name, records, fixture.expected)
