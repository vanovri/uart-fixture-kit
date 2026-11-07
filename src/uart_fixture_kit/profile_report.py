"""Standalone HTML view of measured profile JSON. No scripts or network calls."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import escape
from typing import Any


def _format_ms(nanoseconds: float) -> str:
    return f"{nanoseconds / 1_000_000:.3f}"


def _render_workload_rows(workloads: Sequence[Mapping[str, Any]]) -> str:
    rows: list[str] = []
    for workload in workloads:
        reference = workload["timings"]["bytewise_reference"]
        candidate = workload["timings"]["chunk_scanning"]
        ratio = workload["speed_ratio"]
        ratio_text = f"{ratio:.2f}x" if ratio is not None else "N/A"
        rows.append(
            f"<tr><th>{escape(str(workload['name']))}"
            f"<small>{workload['bytes']:,} bytes / {workload['events']:,} events</small></th>\n"
            f"<td>{_format_ms(reference['median_ns'])}"
            f"<small>{_format_ms(reference['min_ns'])}–{_format_ms(reference['max_ns'])}</small></td>\n"
            f"<td>{_format_ms(candidate['median_ns'])}"
            f"<small>{_format_ms(candidate['min_ns'])}–{_format_ms(candidate['max_ns'])}</small></td>\n"
            f"<td>{ratio_text}</td></tr>"
        )
    return "".join(rows)


def _render_fixture_hashes(workloads: Sequence[Mapping[str, Any]]) -> str:
    return "".join(
        f"<li><strong>{escape(str(w['name']))}</strong>"
        f"<code>{escape(str(w['fixture_sha256']))}</code></li>"
        for w in workloads
    )


def _render_hotspot_sections(hotspots: Mapping[str, Sequence[Mapping[str, Any]]]) -> str:
    sections: list[str] = []
    for name, values in hotspots.items():
        entries = "".join(
            f"<tr><td>{escape(str(item['file']))}:{item['line']} {escape(str(item['function']))}</td>"
            f"<td>{item['calls']:,}</td><td>{item['cumulative_seconds'] * 1000:.3f}</td></tr>"
            for item in values[:7]
        )
        sections.append(
            f"<h3>{escape(str(name))}</h3>"
            f"<table><thead><tr><th>Function</th><th>Calls</th><th>Cumulative ms</th></tr></thead>"
            f"<tbody>{entries}</tbody></table>"
        )
    return "".join(sections)


def render_profile_html(report: Mapping[str, Any]) -> str:
    workloads = report["workloads"]
    environment = report["environment"]
    params = report["parameters"]
    checks = report["gate"]["checks"]
    passed = sum(bool(check["passed"]) for check in checks)
    rows_html = _render_workload_rows(workloads)
    hashes_html = _render_fixture_hashes(workloads)
    hotspots_html = _render_hotspot_sections(report["hotspots"])
    gate_label = "passed" if report["passed"] else "passed; timing withheld"
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>UART Parse Profiler</title><style>
:root{{font-family:system-ui,sans-serif;color:#142f40;background:#fafaf7}}*{{box-sizing:border-box}}body{{margin:0}}main{{max-width:1120px;margin:auto;padding:52px 24px}}header{{display:flex;justify-content:space-between;gap:20px;font-size:13px}}h1{{font-size:clamp(36px,5vw,60px);letter-spacing:-.04em;margin:52px 0 14px}}p{{line-height:1.7;max-width:850px}}.label{{color:#386452;font-weight:600}}.gate{{background:#142f40;color:#fafaf7;padding:24px 28px;margin:30px 0}}.gate strong{{font-size:28px}}.table-wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;margin:22px 0;text-align:left}}th,td{{border-bottom:1px solid #d4dcdd;padding:18px 12px;vertical-align:top}}thead th{{font-size:13px}}td{{font-variant-numeric:tabular-nums}}small{{display:block;font-size:12px;line-height:1.7;color:#526474;font-weight:400;margin-top:5px}}.note{{font-size:14px;color:#526474}}h2{{font-size:24px;margin-top:46px}}code{{font-size:12px;overflow-wrap:anywhere;display:block;margin:8px 0 20px}}details{{margin-top:28px}}summary{{cursor:pointer;padding:14px 0}}ul{{list-style:none;padding:0}}footer{{border-top:1px solid #d4dcdd;margin-top:45px;padding-top:20px;font-size:13px}}a{{color:inherit}}@media(max-width:600px){{main{{padding:28px 16px}}header{{flex-direction:column;gap:6px}}th,td{{padding:14px 9px}}table{{min-width:640px}}h1{{margin-top:35px}}}}
</style><main><header><strong>uart-fixture-kit / Parse Profiler</strong><span>{escape(str(report['created_utc']))}</span></header>
<h1>Parser output first.<br>Timing second.</h1><p class="label">Synthetic byte streams. Virtual arrival times. Actual local measurements.</p>
<p>The unchanged byte-wise JSON reference parser is compared with a chunk-scanning alternative. Every timed run must preserve the expected records, including diagnostics.</p>
<section class="gate"><strong>{passed} / {len(checks)} correctness checks {gate_label}</strong><p>Bundled JSON fixtures and four generated workloads run through both parsers before measurement.</p></section>
<h2>Paired timing results</h2><p class="note">Median milliseconds. The smaller line shows min–max over {params['repeats']} measured runs after {params['warmups']} warm-ups. Ratio = reference median / chunk-scanning median. Values below 1 mean the alternative was slower.</p>
<div class="table-wrap"><table><thead><tr><th>Workload</th><th>Reference ms</th><th>Chunk scanning ms</th><th>Ratio</th></tr></thead><tbody>{rows_html}</tbody></table></div>
<h2>Measurement conditions</h2><p>{escape(str(environment['implementation']))} {escape(str(environment['python']))}, {escape(str(environment['platform']))}, {environment['logical_cpus']} logical CPUs.</p>
<p class="note">{escape(str(environment['processor']))}. Timer: perf_counter_ns. Parser construction, event dispatch and record creation are timed. Exact JSON comparison runs outside the timer. Paired order alternates. Garbage collection is disabled during each timed run and restored afterward. This is one machine, without process isolation or a CPU-affinity setting. No serial device, electrical timing or hardware speed is measured.</p>
<details><summary>cProfile hotspots, bulk workload</summary><p class="note">A separate instrumented run. Cumulative times overlap and must not be added or compared directly with the uninstrumented timing table.</p>{hotspots_html}</details>
<details><summary>Fixture and parser SHA256 evidence</summary><p class="note">Fixture digests cover canonical event bytes, virtual timestamps, expected records and name. Source digests cover the two class definitions; the alternative inherits timer and EOF handling from the reference.</p><h3>Parser sources</h3><code>{escape(str(report['sources']['reference_sha256']))}</code><code>{escape(str(report['sources']['candidate_sha256']))}</code><ul>{hashes_html}</ul></details>
<footer><a href="https://github.com/vanovri/uart-fixture-kit">Source and contracts</a>. Developed with Codex assistance. Results depend on workload and environment.</footer></main></html>'''
