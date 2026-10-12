# Contributing

Useful contributions start with a reproducible parser failure. Open an issue with the expected behavior, Python version, and a minimal synthetic byte sequence. Explain whether the issue concerns the replay contract, a reference parser, or your own adapter.

Do not upload personal telemetry, private captures, credentials, patient data, or code without permission. Replace values with synthetic data while preserving the framing failure.

For pull requests:

1. Add a small fixture or test that fails before the fix.
2. Change only the relevant implementation.
3. Run `python -m pytest`, `uart-fixture-kit demo`, and `python -m build`.
4. Explain the behavior and any compatibility changes.

Keep the core independent of hardware, serial drivers, web frameworks, and AI services. New example protocols should remain clearly documented examples. Prefer an adapter and fixture over a new transport framework.

AI-assisted contributions are welcome. Contributors remain responsible for licensing, correctness, and explaining their changes.
