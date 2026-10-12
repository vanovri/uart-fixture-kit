"""Explicit, deterministic byte mutations and arrival schedules."""

from itertools import cycle
from typing import Iterable

from .core import Event, _milliseconds


def chunk_bytes(data: bytes, sizes: Iterable[int]) -> tuple[bytes, ...]:
    """Split by a repeating positive-size schedule, preserving all bytes."""
    sizes = tuple(sizes)
    if not sizes or any(type(size) is not int or size < 1 for size in sizes):
        raise ValueError("sizes must contain positive integers")
    chunks = []
    offset = 0
    for size in cycle(sizes):
        if offset >= len(data):
            break
        chunks.append(data[offset:offset + size])
        offset += size
    return tuple(chunks)


def schedule(data: bytes, sizes: Iterable[int], *, start_ms: int = 0, interval_ms: int = 1) -> tuple[Event, ...]:
    """Assign virtual arrival times to chunks. Zero interval means concatenated arrival."""
    _milliseconds(start_ms, "start_ms")
    _milliseconds(interval_ms, "interval_ms")
    return tuple(Event(start_ms + index * interval_ms, chunk)
                 for index, chunk in enumerate(chunk_bytes(data, sizes)))


def flip_byte(data: bytes, offset: int, mask: int = 1) -> bytes:
    """XOR one byte at an explicit zero-based offset."""
    if type(offset) is not int or not 0 <= offset < len(data):
        raise ValueError("offset must point to an existing byte")
    if type(mask) is not int or not 1 <= mask <= 255:
        raise ValueError("mask must be between 1 and 255")
    return data[:offset] + bytes([data[offset] ^ mask]) + data[offset + 1:]


def drop_bytes(data: bytes, offset: int, count: int = 1) -> bytes:
    """Drop a bounded byte range; no random state or seed is involved."""
    if type(offset) is not int or type(count) is not int or count < 1 or not 0 <= offset < len(data) or offset + count > len(data):
        raise ValueError("drop range must lie inside data and contain at least one byte")
    return data[:offset] + data[offset + count:]
