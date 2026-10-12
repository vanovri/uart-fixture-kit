import json

import pytest

from uart_fixture_kit import Event, Fixture, flip_byte, replay, schedule
from uart_fixture_kit.examples import BinaryParser, JsonLineParser, encode_binary


def msg(seq, value):
    return {"kind": "message", "seq": seq, "value": value, "unit": "C"}


def diag(code):
    return {"kind": "diagnostic", "code": code}


LINE = b'{"seq":1,"value":25.0,"unit":"C"}\n'
FRAMES = bytes.fromhex("aa550301c409d1aa5503021efb1e")


@pytest.mark.parametrize("split", range(len(LINE) + 1))
def test_json_every_two_chunk_split(split):
    replay(Fixture("split", (Event(0, LINE[:split]), Event(1, LINE[split:])), 2,
                   [msg(1,25.0)]), JsonLineParser()).assert_matches()


@pytest.mark.parametrize("split", range(len(FRAMES) + 1))
def test_binary_every_two_chunk_split(split):
    replay(Fixture("split", (Event(0, FRAMES[:split]), Event(1, FRAMES[split:])), 2,
                   [msg(1,25.0), msg(2,-12.5)]), BinaryParser()).assert_matches()


@pytest.mark.parametrize("sizes", [[1], [2,3,1], [1024]])
def test_binary_recovery_is_chunk_invariant(sizes):
    bad = flip_byte(FRAMES[:7], 6)
    events = schedule(b"noise" + bad + FRAMES[7:], sizes)
    replay(Fixture("recovery", events, events[-1].at_ms + 1,
                   [diag("bad_checksum"), msg(2,-12.5)]), BinaryParser()).assert_matches()


@pytest.mark.parametrize("time,expected", [(49,"truncated"), (50,"stale_partial"), (51,"stale_partial")])
def test_stale_exact_boundary(time, expected):
    replay(Fixture("boundary", (Event(0,b'{"seq":'),), time,
                   [diag(expected)]), JsonLineParser()).assert_matches()


def test_timeout_is_inactivity_not_total_frame_age():
    events = (Event(0, LINE[:10]), Event(40, LINE[10:20]), Event(80, LINE[20:]))
    replay(Fixture("slow-active",events,81,[msg(1,25.0)]), JsonLineParser()).assert_matches()


@pytest.mark.parametrize("record", [
    {"seq": True, "value":25, "unit":"C"}, {"seq":256,"value":25,"unit":"C"},
    {"seq":1,"value":True,"unit":"C"}, {"seq":1,"value":"25","unit":"C"},
    {"seq":1,"value":float("inf"),"unit":"C"}, {"seq":1,"value":25,"unit":"mV"}, [],
])
def test_invalid_record(record):
    data = (json.dumps(record)+"\n").encode()
    replay(Fixture("invalid",(Event(0,data),),1,[diag("invalid_record")]), JsonLineParser()).assert_matches()


def test_invalid_utf8_recovers():
    replay(Fixture("encoding", (Event(0,b"\xff\n"+LINE),),1,
                   [diag("invalid_json"),msg(1,25.0)]), JsonLineParser()).assert_matches()


def test_oversize_without_newline_stays_bounded_and_recovers():
    parser = JsonLineParser()
    assert parser.feed(b"x"*10000,0) == [diag("oversize")]
    assert len(parser.buffer) == 0
    assert parser.feed(b"\n"+LINE,1) == [msg(1,25.0)]


def test_oversize_state_clears_at_timeout():
    replay(Fixture("oversize-stale",(Event(0,b"x"*129),Event(50,LINE)),51,
                   [diag("oversize"),diag("stale_partial"),msg(1,25.0)]), JsonLineParser()).assert_matches()


def test_binary_encode_matches_independent_golden_bytes():
    assert encode_binary(1,2500) == bytes.fromhex("aa550301c409d1")
    assert encode_binary(2,-1250) == bytes.fromhex("aa5503021efb1e")


def test_bad_candidate_can_contain_start_of_next_frame():
    data = bytes.fromhex("aa5503") + FRAMES[:7]
    replay(Fixture("nested-sync",(Event(0,data),),1,
                   [diag("bad_checksum"),msg(1,25.0)]), BinaryParser()).assert_matches()


def test_binary_noise_does_not_accumulate():
    parser = BinaryParser()
    assert parser.feed(b"x"*10000 + b"\xaa",0) == []
    assert len(parser.buffer) == 1


def test_payload_may_contain_sync_marker():
    # 0x55AA little-endian payload contains the magic bytes AA 55.
    frame = encode_binary(1,0x55AA)
    replay(Fixture("payload-sync",schedule(frame,[1]),7,[msg(1,219.3)]), BinaryParser()).assert_matches()
