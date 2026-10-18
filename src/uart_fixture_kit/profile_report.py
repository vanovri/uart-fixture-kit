"""Standalone HTML view of measured profile JSON. No scripts or network calls."""

from html import escape


def render_profile_html(report: dict) -> str:
    rows = []
    for workload in report["workloads"]:
        reference = workload["timings"]["bytewise_reference"]
        candidate = workload["timings"]["chunk_scanning"]
        ratio = workload["speed_ratio"]
        def milliseconds(value):
            return f"{value / 1_000_000:.3f}"
        rows.append(f'''<tr><th>{escape(workload['name'])}<small>{workload['bytes']:,} bytes / {workload['events']:,} events</small></th>
<td>{milliseconds(reference['median_ns'])}<small>{milliseconds(reference['min_ns'])}–{milliseconds(reference['max_ns'])}</small></td>
<td>{milliseconds(candidate['median_ns'])}<small>{milliseconds(candidate['min_ns'])}–{milliseconds(candidate['max_ns'])}</small></td>
<td>{f'{ratio:.2f}x' if ratio is not None else 'N/A'}</td></tr>''')
    environment = report["environment"]
    params = report["parameters"]
    passed = sum(check["passed"] for check in report["gate"]["checks"])
    hashes = "".join(f"<li><strong>{escape(w['name'])}</strong><code>{escape(w['fixture_sha256'])}</code></li>"
                     for w in report["workloads"])
    hotspots = []
    for name, values in report["hotspots"].items():
        entries = "".join(f"<tr><td>{escape(item['file'])}:{item['line']} {escape(item['function'])}</td>"
                          f"<td>{item['calls']:,}</td><td>{item['cumulative_seconds'] * 1000:.3f}</td></tr>"
                          for item in values[:7])
        hotspots.append(f"<h3>{escape(name)}</h3><table><thead><tr><th>Function</th><th>Calls</th><th>Cumulative ms</th></tr></thead><tbody>{entries}</tbody></table>")
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>UART Parse Profiler</title><style>
:root{{font-family:system-ui,sans-serif;color:#142f40;background:#fafaf7}}*{{box-sizing:border-box}}body{{margin:0}}main{{max-width:1120px;margin:auto;padding:52px 24px}}header{{display:flex;justify-content:space-between;gap:20px;font-size:13px}}h1{{font-size:clamp(36px,5vw,60px);letter-spacing:-.04em;margin:52px 0 14px}}p{{line-height:1.7;max-width:850px}}.label{{color:#386452;font-weight:600}}.gate{{background:#142f40;color:#fafaf7;padding:24px 28px;margin:30px 0}}.gate strong{{font-size:28px}}.table-wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;margin:22px 0;text-align:left}}th,td{{border-bottom:1px solid #d4dcdd;padding:18px 12px;vertical-align:top}}thead th{{font-size:13px}}td{{font-variant-numeric:tabular-nums}}small{{display:block;font-size:12px;line-height:1.7;color:#526474;font-weight:400;margin-top:5px}}.note{{font-size:14px;color:#526474}}h2{{font-size:24px;margin-top:46px}}code{{font-size:12px;overflow-wrap:anywhere;display:block;margin:8px 0 20px}}details{{margin-top:28px}}summary{{cursor:pointer;padding:14px 0}}ul{{list-style:none;padding:0}}footer{{border-top:1px solid #d4dcdd;margin-top:45px;padding-top:20px;font-size:13px}}a{{color:inherit}}@media(max-width:600px){{main{{padding:28px 16px}}header{{flex-direction:column;gap:6px}}th,td{{padding:14px 9px}}table{{min-width:640px}}h1{{margin-top:35px}}}}
</style><main><header><strong>uart-fixture-kit / Parse Profiler</strong><span>{escape(report['created_utc'])}</span></header>
<h1>Parser output first.<br>Timing second.</h1><p class="label">Synthetic byte streams. Virtual arrival times. Actual local measurements.</p>
<p>The unchanged byte-wise JSON reference parser is compared with a chunk-scanning alternative. Every timed run must preserve the expected records, including diagnostics.</p>
<section class="gate"><strong>{passed} / {len(report['gate']['checks'])} correctness checks {'passed' if report['passed'] else 'passed; timing withheld'}</strong><p>Bundled JSON fixtures and four generated workloads run through both parsers before measurement.</p></section>
<h2>Paired timing results</h2><p class="note">Median milliseconds. The smaller line shows min–max over {params['repeats']} measured runs after {params['warmups']} warm-ups. Ratio = reference median / chunk-scanning median. Values below 1 mean the alternative was slower.</p>
<div class="table-wrap"><table><thead><tr><th>Workload</th><th>Reference ms</th><th>Chunk scanning ms</th><th>Ratio</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<h2>Measurement conditions</h2><p>{escape(environment['implementation'])} {escape(environment['python'])}, {escape(environment['platform'])}, {environment['logical_cpus']} logical CPUs.</p>
<p class="note">{escape(environment['processor'])}. Timer: perf_counter_ns. Parser construction, event dispatch and record creation are timed. Exact JSON comparison runs outside the timer. Paired order alternates. Garbage collection is disabled during each timed run and restored afterward. This is one machine, without process isolation or a CPU-affinity setting. No serial device, electrical timing or hardware speed is measured.</p>
<details><summary>cProfile hotspots, bulk workload</summary><p class="note">A separate instrumented run. Cumulative times overlap and must not be added or compared directly with the uninstrumented timing table.</p>{''.join(hotspots)}</details>
<details><summary>Fixture and parser SHA256 evidence</summary><p class="note">Fixture digests cover canonical event bytes, virtual timestamps, expected records and name. Source digests cover the two class definitions; the alternative inherits timer and EOF handling from the reference.</p><h3>Parser sources</h3><code>{escape(report['sources']['reference_sha256'])}</code><code>{escape(report['sources']['candidate_sha256'])}</code><ul>{hashes}</ul></details>
<footer><a href="https://github.com/vanovri/uart-fixture-kit">Source and contracts</a>. Developed with Codex assistance. Results depend on workload and environment.</footer></main></html>'''
