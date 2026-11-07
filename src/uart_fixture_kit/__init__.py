"""Replay byte fixtures against a parser without hardware or wall-clock sleeps."""

from __future__ import annotations

from .core import Event, Fixture, Parser, ReplayResult, load_fixture, replay
from .faults import chunk_bytes, drop_bytes, flip_byte, schedule

__all__ = [
    "Event",
    "Fixture",
    "Parser",
    "ReplayResult",
    "chunk_bytes",
    "drop_bytes",
    "flip_byte",
    "load_fixture",
    "replay",
    "schedule",
]
__version__ = "0.3.0"
