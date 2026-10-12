# uart-fixture-kit

[![Tests](https://github.com/vanovri/uart-fixture-kit/actions/workflows/tests.yml/badge.svg)](https://github.com/vanovri/uart-fixture-kit/actions/workflows/tests.yml)

Reproduce byte-stream parser bugs without a serial device. Save raw bytes, their virtual arrival times, and expected records in JSON. Replay the same fixture against your own Python parser in pytest or from the command line.

A serial read can return half a message, several messages, or noise followed by a valid frame. A parser that works with complete lines can still fail when those lines arrive in chunks. This toolkit makes that failure small, repeatable, and reviewable.

## Try it

Python 3.10 or newer. No runtime dependencies, API keys, GPU, or hardware.

```console
python -m pip install "git+https://github.com/vanovri/uart-fixture-kit.git@v0.1.0"
uart-fixture-kit demo
uart-fixture-kit demo --json
```

The demo runs bundled synthetic cases for newline JSON and a small binary protocol: complete, fragmented and concatenated messages, malformed JSON, invalid values and units, oversized lines, EOF truncation, stale partial input, bad frame length, checksum failure, resynchronization, duplicate sequence and sequence wraparound.

GitHub is the distribution source for this release. This package has not been published on PyPI.

## Catch a real class of bug

This fixture delivers `OK\n` in two reads:

```python
from uart_fixture_kit import Event, Fixture, Parser, replay

fixture = Fixture(
    name="split-line",
    events=(Event(0, b"O"), Event(1, b"K\n")),
    end_ms=2,
    expected=[{"line": "OK"}],
)

class Broken(Parser):
    def feed(self, data, now_ms):
        # Mistake: assume every read contains complete lines.
        return [{"line": part.decode()} for part in data.splitlines()]

result = replay(fixture, Broken())
assert not result.passed  # Actual records: O, K. Expected: OK.
print(result.diff)
```

Keep unfinished bytes between reads:

```python
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

replay(fixture, Buffered()).assert_matches()
```

## Test your existing parser

Subclass `Parser` and adapt your parser's decoded messages to JSON object records. The toolkit does not impose a wire format or decide which bytes are valid for your application.

```python
from uart_fixture_kit import Parser, load_fixture, replay

class Adapter(Parser):
    def __init__(self):
        self.decoder = YourExistingDecoder()

    def feed(self, data, now_ms):
        return [{"value": message.value} for message in self.decoder.feed(data)]

def test_uart_contract():
    replay(load_fixture("tests/fixtures/split-message.json"), Adapter()).assert_matches()
```

For a CLI adapter, put a zero-argument factory or class in an importable module:

```console
uart-fixture-kit run tests/fixtures/split-message.json --parser my_adapter:Adapter --json
```

The adapter runs as ordinary trusted local Python code. Only run adapter modules you trust. Fixture JSON supplies bytes and expected data, never executable code.

## Fixture format

```json
{
  "version": 1,
  "name": "split-line",
  "events": [
    {"at_ms": 0, "hex": "4f"},
    {"at_ms": 1, "hex": "4b0a"}
  ],
  "end_ms": 2,
  "expected": [{"line": "OK"}]
}
```

- `hex` holds the exact received bytes. Spaces are allowed.
- Times are non-negative integer milliseconds, ordered, and no later than `end_ms`. Equal arrival times are allowed; list order is preserved.
- `advance(now_ms)` runs before each `feed(data, now_ms)` and again at `end_ms`. An event with empty `hex` is an explicit timer tick. No real time passes.
- `finish(end_ms)` runs once. Override it to report incomplete data at EOF.
- Every method emits zero or more JSON object records. `advance` and `finish` default to no output.
- Expected records compare exactly, in order, using sorted-key JSON. Numeric spelling such as `1` versus `1.0` differs; object key order does not. Non-finite values are rejected.
- A fresh parser instance is required for each replay. Parser exceptions retain their tracebacks through the Python API.

CLI exit codes: `0` all contracts match, `1` records differ, `2` fixture/import/report error. Unexpected parser exceptions exit with a traceback.

## Build fault cases

```python
from uart_fixture_kit import chunk_bytes, drop_bytes, flip_byte, schedule

chunks = chunk_bytes(b"OK\nREADY\n", [1, 2, 3])  # Repeating explicit sizes.
events = schedule(b"OK\n", [1], interval_ms=10)
corrupted = flip_byte(b"abc", offset=1, mask=1)
missing = drop_bytes(b"abc", offset=1, count=1)
```

These helpers have no random state. Store the resulting hex and arrival times in a fixture to share a failure. Duplicate input by inserting the same event or frame twice.

## Reference examples and limits

`JsonLineParser` accepts newline-delimited synthetic temperature records with `seq` 0..255, a finite numeric `value`, and `unit: "C"`. It limits unfinished lines to 128 bytes and uses a 50 ms inactivity timeout. Timer diagnostics occur at the supplied virtual ticks, not necessarily at the physical threshold instant.

`BinaryParser` uses a demonstration frame: `AA 55 | length=3 | sequence:u8 | centi-C:i16 little-endian | sum8`. The checksum covers length and payload modulo 256. It rejects wrong lengths/checksums, suppresses immediately repeated sequence numbers, and accepts 255-to-0 wraparound. A sequence discontinuity indicates a change in sequence, not proof of packet loss. Sum8 is not a CRC or authentication mechanism.

Both examples are deliberately small. They are not production telemetry standards, device drivers, or clinical software. Fixtures use synthetic bytes only. Software replay cannot validate UART electrical behavior, baud-rate accuracy, ADC timing, hardware reliability, or device safety.

## Development

```console
git clone https://github.com/vanovri/uart-fixture-kit.git
cd uart-fixture-kit
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest
python -m build
```

Tests include a failing broken-parser regression, every two-chunk split of representative streams, byte-by-byte corruption recovery, timer boundary cases, CLI exit codes, fixture validation, and installed-package demo checks. CI runs on Windows and Linux across Python 3.10, 3.12, and 3.14.

## Related tools

[pySerial](https://pyserial.readthedocs.io/en/latest/) provides serial transport and loopback facilities. [Serial Studio](https://github.com/Serial-Studio/Serial-Studio) provides telemetry visualization and capture/replay. [WireDAQ](https://github.com/wuisabel-gif/WireDAQ) includes its own wire protocol and fault simulation. This project focuses on a small fixture contract for adapters to existing parsers. Replay and fault injection are established techniques.

## Contribute

Bring a minimal synthetic failing fixture and its expected output. See [CONTRIBUTING.md](CONTRIBUTING.md). MIT licensed. Initial implementation was created with AI coding assistance and checked by executable tests; no claim of entirely human-written code.
