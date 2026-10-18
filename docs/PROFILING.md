# UART Parse Profiler

This extends the existing synthetic parser fixture kit. The reference `JsonLineParser` remains unchanged. `ChunkedJsonLineParser` scans for newlines using `bytes.find` and appends bounded slices instead of looping over every byte in Python. Both retain unfinished bytes, reject lines longer than 128 bytes, recover after newline or inactivity, validate fields and emit the same diagnostic records. The alternative inherits the reference's `advance` and `finish` methods.

## Replay the comparison

```console
python -m uart_fixture_kit profile --records 2048 --repeats 9 --warmups 3 --output uart-profile.json --html uart-profile.html
```

This runs two parsers over seven bundled JSON fixtures and four generated workloads, giving 22 correctness comparisons. A mismatch prevents timing entirely. Subsequent warm-ups, measured runs and cProfile runs must also preserve canonical JSON output exactly. Boolean and numeric validation, diagnostic order and unfinished-buffer behavior belong to the contract, not just decoded values.

The three valid-data workloads use the same 2,048 records and 76,739 bytes. Only chunk boundaries and the resulting virtual event schedule differ: chunks of up to 4,096 bytes, a repeating 1/2/7/3 schedule, and individual bytes. The fourth workload has an explicit oracle for an expired partial record, invalid UTF-8, an oversized line, a bad unit, a valid message, and an EOF fragment. Workload creation is deterministic and uses no random state. Additional seeded differential tests explore mixed-stream chunk boundaries, alongside independent saved oracles and line-boundary cases.

`fixture_manifest` and each workload hash cover canonical JSON containing the fixture name, exact event bytes, timestamps, end time and expected records. The report hashes both parser class definitions. Because the alternative inherits timer/EOF methods, keep the reference source hash with the alternative's hash. Reports record Python, OS, CPU information, repeat count and warm-up count. Source and fixture hashes identify what ran, not physical-device validity.

## What the timer measures

The `perf_counter_ns` interval includes constructing a fresh parser, dispatching virtual `advance`/`feed`/`finish` calls and collecting emitted records. It excludes fixture generation, JSON equivalence checks, hashing, report creation and cProfile. Virtual timestamps are inputs to parser logic, not wall-clock sleeps.

Both parsers receive warm-ups before measurement. Each repeat is paired. The reference runs first on even-numbered iterations and second on odd-numbered iterations to reduce a fixed ordering bias. Garbage collection is disabled only during each timed run and restored to its previous state afterward. Raw nanosecond samples are retained, with median, minimum, maximum, population standard deviation and nearest-rank observed p95. With nine samples, p95 is the maximum observed sample, not a latency-distribution estimate.

`speed_ratio` is reference median divided by alternative median. Above 1 means the alternative took less median time. `candidate_time_change_percent` is (alternative/reference - 1) times 100, so a positive number means a slowdown. These ratios use measured parser times, not a device's baud rate. Small recovery timings and small differences are especially sensitive to noise.

A separate cProfile run uses the bulk workload. Its cumulative timings overlap and include instrumentation overhead. Do not add cumulative times or compare them directly with uninstrumented wall times. It helps locate Python-level work; it does not prove the entire application sped up.

## Observed example

The checked-in `profile-sample.json` and HTML came from one run on 18 October 2026 WIB, CPython 3.14.7, Windows 11, Intel64 Family 6 Model 165, with eight logical CPUs. With nine measured repeats and three warm-ups:

- Bulk chunk median: 18.368 ms reference, 8.669 ms alternative, ratio 2.12x.
- Fragmented median: 27.723 ms reference, 26.099 ms alternative, ratio 1.06x.
- Byte-wise median: 44.280 ms reference, 56.507 ms alternative, ratio 0.78x.
- Fault-recovery median: 0.064 ms reference, 0.046 ms alternative, ratio 1.38x.

The alternative was slower on one-byte events. It remains opt-in. Larger chunks allow the C-level search to avoid much of the Python byte loop, while tiny chunks still pay search and method-call overhead. On the bulk cProfile run, the reference made 74,691 `bytearray.append` calls. That observation supports the mechanism being explored; it does not establish the same ratio on another machine.

There is no CPU-affinity setting, process isolation, thermal control or hardware I/O. Other applications may affect the results. The HTML and JSON deliberately preserve the slower case. Re-run on your target workload before choosing a parser. This synthetic comparison does not validate electrical UART behavior, sensor timing, production reliability or clinical use.

## Provenance

The source, workload definitions and record oracles are original synthetic additions for this project, developed with Codex assistance. They contain no shared thesis code, private captures or subject data. The broad theme of profiling before accepting an optimization was inspired by [Google's account of optimizing the ADK Python codebase](https://developers.googleblog.com/optimizing-a-200000-line-python-codebase-overnight-with-gemini-4-argon/). This project does not run or benchmark Gemini, and none of Google's performance figures are reused as local results.
