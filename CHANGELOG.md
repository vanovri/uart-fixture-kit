# Changelog

## 0.3.0 - 2026-10-19

- Add an offline Twin Bench with eight synthetic loss, corruption, and delay combinations.
- Replay bytes through the existing binary parser, preserving receiver logs and data/source hashes.
- Hold back older samples and label generation-based freshness rather than refreshing age on late arrival.
- Add a self-contained 2D/3D data glyph, replay controls, JSON export, Python model tests, and Node view tests.

## 0.2.0

- Chunk-scanning JSON alternative, preserving the byte-wise reference parser.
- Correctness-gated parser profiles with fixed synthetic workloads, raw paired timing samples, source/fixture hashes and cProfile hotspots.
- Standalone HTML profile reports and installed-wheel CLI verification in CI.

## 0.1.0

- Protocol-independent JSON byte fixtures and virtual-time replay.
- Python adapter API, assertion diffs, and CLI JSON reports.
- Explicit chunking, byte deletion, and XOR mutation helpers.
- Synthetic JSON-line and binary reference parsers and failure fixtures.
- Regression tests and Windows/Linux CI.
