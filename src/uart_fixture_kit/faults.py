"""Explicit, deterministic byte mutations and arrival schedules."""

from __future__ import annotations

from collections.abc import Iterable
from itertools import cycle

from .core import Event, _milliseconds


def _require_bytes(data: object) -> bytes:
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    return data


def chunk_bytes(data: bytes, sizes: Iterable[int]) -> tuple[bytes, ...]:
    """Split by a repeating positive-size schedule, preserving all bytes."""
    raw = _require_bytes(data)
    size_schedule = tuple(sizes)
    if not size_schedule or any(type(size) is not int or size < 1 for size in size_schedule):
        raise ValueError("sizes must contain positive integers")
    chunks: list[bytes] = []
    offset = 0
    total = len(raw)
    for size in cycle(size_schedule):
        if offset >= total:
            break
        chunks.append(raw[offset : offset + size])
        offset += size
    return tuple(chunks)


def schedule(
    data: bytes,
    sizes: Iterable[int],
    *,
    start_ms: int = 0,
    interval_ms: int = 1,
) -> tuple[Event, ...]:
    """Assign virtual arrival times to chunks. Zero interval means concatenated arrival."""
    _require_bytes(data)
    _milliseconds(start_ms, "start_ms")
    _milliseconds(interval_ms, "interval_ms")
    return tuple(
        Event(start_ms + index * interval_ms, chunk)
        for index, chunk in enumerate(chunk_bytes(data, sizes))
    )


def flip_byte(data: bytes, offset: int, mask: int = 1) -> bytes:
    """XOR one byte at an explicit zero-based offset."""
    raw = _require_bytes(data)
    if type(offset) is not int or not 0 <= offset < len(raw):
        raise ValueError("offset must point to an existing byte")
    if type(mask) is not int or not 1 <= mask <= 255:
        raise ValueError("mask must be between 1 and 255")
    return raw[:offset] + bytes([raw[offset] ^ mask]) + raw[offset + 1 :]


def drop_bytes(data: bytes, offset: int, count: int = 1) -> bytes:
    """Drop a bounded byte range; no random state or seed is involved."""
    raw = _require_bytes(data)
    if (
        type(offset) is not int
        or type(count) is not int
        or count < 1
        or not 0 <= offset < len(raw)
        or offset + count > len(raw)
    ):
        raise ValueError("drop range must lie inside data and contain at least one byte")
    return raw[:offset] + raw[offset + count :]
