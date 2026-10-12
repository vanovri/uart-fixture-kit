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
    args = parser.parse_args(argv)
    reports = []
    try:
        if args.command == "demo":
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
