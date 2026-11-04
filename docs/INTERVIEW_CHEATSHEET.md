# UART Fixture Kit — Technical Interview Cheatsheet

## What this repository actually implements

The production reference `BinaryParser` in `src/uart_fixture_kit/examples.py` is
deliberately synthetic:

```text
AA 55 | length=3 | sequence:u8 | centi-C:i16 little-endian | sum8
```

Its checksum is `sum(length + payload) mod 256`; it is **not** CRC-16 and does
not provide authentication. The test file
`tests/test_fault_injection_and_interview_drills.py` contains a separate,
interview-oriented receiver model for the requested `AA 55 + CRC-16-CCITT`
protocol. This distinction prevents a demo checksum from being presented as a
production integrity guarantee.

## Receiver FSM

| State | Input/event | Action | Next state |
| --- | --- | --- | --- |
| `SEARCH_SYNC` | byte is not `0xAA` | discard byte | `SEARCH_SYNC` |
| `SEARCH_SYNC` | byte is `0xAA` | retain candidate | `WAIT_SYNC_TAIL` |
| `WAIT_SYNC_TAIL` | byte is not `0x55` | discard candidate and rescan | `SEARCH_SYNC` |
| `WAIT_SYNC_TAIL` | byte is `0x55` | accept sync word | `READ_LENGTH` |
| `READ_LENGTH` | length unavailable | wait for more bytes | `READ_LENGTH` |
| `READ_LENGTH` | length invalid or over limit | report framing error; rescan | `SEARCH_SYNC` |
| `READ_PAYLOAD` | frame incomplete | retain bounded buffer | `READ_PAYLOAD` |
| `READ_PAYLOAD` | payload and CRC available | calculate and compare CRC | `CHECK_CRC` |
| `CHECK_CRC` | CRC mismatch | report CRC error; advance one byte and rescan | `SEARCH_SYNC` |
| `CHECK_CRC` | CRC matches | emit payload; remove frame | `SEARCH_SYNC` |

The key design rule is to treat serial reads as arbitrary byte chunks. A read
may contain one byte, half a frame, multiple frames, or noise around valid
frames. A false `0xAA` inside payload is data because the length field defines
the frame boundary.

## CRC-16-CCITT calculation

The interview drill uses CRC-16-CCITT-FALSE:

```text
polynomial = 0x1021
initial    = 0xFFFF
input/output reflection = false
final XOR  = 0x0000
```

For each byte:

1. XOR the byte into the high eight bits of the 16-bit register.
2. Repeat eight times:
   - if bit 15 is set, shift left and XOR `0x1021`;
   - otherwise shift left only.
3. Keep the register masked to 16 bits.

The standard check vector is:

```text
CRC16_CCITT("123456789") = 0x29B1
```

The wire encoding in the drill is:

```text
AA 55 | payload_length:u8 | payload | crc:u16 big-endian
```

The CRC covers `payload_length + payload`, not the sync word. The sender and
receiver must agree on byte order and coverage.

## Buffer overflow and framing errors

- **Bound the buffer:** reject lengths above the configured maximum and cap
  retained bytes. This prevents a malformed length or continuous noise from
  consuming unbounded RAM.
- **Resynchronize locally:** after a bad candidate, discard one byte and scan
  again. Do not discard the entire stream; the next valid `AA 55` may already
  be buffered.
- **Preserve a possible sync prefix:** when trimming an overfull buffer, keep a
  bounded tail so a final `0xAA` can become the first byte of a future sync
  word.
- **Separate diagnostics:** `framing_error`, `crc_error`, `buffer_overflow`,
  and successful frame delivery should be observable independently.
- **Avoid false recovery:** never search for sync inside a candidate payload
  before its declared length is consumed.

## Repository test strategy

The existing suite covers fixture replay, JSON and synthetic binary parser
recovery, timeout boundaries, checksum recovery, CLI contracts, profiling, and
the offline twin bench. The interview drill adds:

- byte-at-a-time starvation and arbitrary multi-chunk reassembly;
- coalesced frames with `0xFF`, `0x00`, and false sync-looking bytes;
- single- and double-bit CRC corruption;
- bounded-buffer recovery and framing diagnostics;
- a deterministic 10,000-frame throughput smoke benchmark.

No test claims UART electrical correctness, baud-rate tolerance, DMA behavior,
hardware reliability, or a production safety guarantee.
