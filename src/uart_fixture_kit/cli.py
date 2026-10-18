"""Local replay CLI. Custom adapters are trusted, locally executed Python code."""

import argparse
from importlib import import_module, resources
import json
from pathlib import Path
import sys

from .core import load_fixture, replay
from .examples import BinaryParser, JsonLineParser


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Replay UART parser fixtures without hardware")
    subcommands = parser.add_subparsers(dest="command", required=True)
    demo = subcommands.add_parser("demo", help="run bundled synthetic fixtures")
    demo.add_argument("--json", action="store_true", help="emit a machine-readable report")
    run = subcommands.add_parser("run", help="test a local fixture using a parser factory")
    run.add_argument("fixture", type=Path)
    run.add_argument("--parser", required=True, metavar="MODULE:FACTORY")
    run.add_argument("--json", action="store_true")
    profile = subcommands.add_parser("profile", help="compare fixed synthetic JSON parser workloads")
    profile.add_argument("--records", type=int, default=2048)
    profile.add_argument("--repeats", type=int, default=7)
    profile.add_argument("--warmups", type=int, default=2)
    profile.add_argument("--output", type=Path, default=Path("uart-profile.json"))
    profile.add_argument("--html", type=Path, help="also write a standalone HTML report")
    profile.set_defaults(json=True)
    twin = subcommands.add_parser("twin", help="export a standalone synthetic UART fault replay")
    twin.add_argument("--frames", type=int, default=120)
    twin.add_argument("--output", type=Path, default=Path("uart-twin.html"))
    twin.add_argument("--json-output", type=Path, help="also save the eight-scenario evidence bank")
    twin.set_defaults(json=True)
    args = parser.parse_args(argv)
    reports = []
    try:
        if args.command == "twin":
            from .twin import build_twin_bank, render_twin_html
            if args.json_output and args.output.resolve() == args.json_output.resolve():
                raise ValueError("HTML and JSON output paths must differ")
            bank = build_twin_bank(args.frames)
            args.output.write_text(render_twin_html(bank), encoding="utf-8")
            if args.json_output:
                args.json_output.write_text(json.dumps(bank, indent=2, allow_nan=False), encoding="utf-8")
            print(json.dumps({"scenarios": len(bank["scenarios"]), "frames": args.frames,
                              "html": str(args.output),
                              "json": str(args.json_output) if args.json_output else None}))
            return 0
        elif args.command == "profile":
            from .profiler import run_profile
            from .profile_report import render_profile_html
            report = run_profile(args.records, args.repeats, args.warmups)
            args.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
            if args.html:
                args.html.write_text(render_profile_html(report), encoding="utf-8")
            print(json.dumps({"passed": report["passed"], "checks": len(report["gate"]["checks"]),
                              "workloads": len(report["workloads"]), "report": str(args.output),
                              "html": str(args.html) if args.html else None}))
            return 0 if report["passed"] else 1
        elif args.command == "demo":
            folder = resources.files("uart_fixture_kit").joinpath("fixtures")
            for resource in sorted(folder.iterdir(), key=lambda item: item.name):
                if not resource.name.endswith(".json"):
                    continue
                with resources.as_file(resource) as path:
                    fixture = load_fixture(path)
                    factory = BinaryParser if path.name.startswith("binary_") else JsonLineParser
                    reports.append(replay(fixture, factory()))
        else:
            module, separator, attribute = args.parser.partition(":")
            if not separator or not module or not attribute:
                raise ValueError("--parser must be MODULE:FACTORY")
            factory = getattr(import_module(module), attribute)
            reports.append(replay(load_fixture(args.fixture), factory()))
    except (ValueError, TypeError, OSError, ImportError, AttributeError) as error:
        if args.json:
            print(json.dumps({"passed": False, "error": str(error)}, allow_nan=False))
        else:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2
    passed = all(result.passed for result in reports)
    if args.json:
        print(json.dumps({"passed": passed, "fixtures": [
            {"name": result.name, "passed": result.passed, "records": result.records,
             "expected": result.expected, "diff": result.diff} for result in reports
        ]}, indent=2, allow_nan=False))
    else:
        for result in reports:
            print(f"{'PASS' if result.passed else 'FAIL'} {result.name}")
            if not result.passed:
                print(result.diff)
        print(f"{sum(result.passed for result in reports)}/{len(reports)} fixtures passed")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
