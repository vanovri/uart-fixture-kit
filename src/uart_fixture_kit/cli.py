"""Local replay CLI. Custom adapters are trusted, locally executed Python code."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Sequence
from importlib import import_module, resources
from pathlib import Path

from .core import Parser, ReplayResult, load_fixture, replay
from .examples import BinaryParser, JsonLineParser

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
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
    return parser


def _write_utf8(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(content)


def _ensure_distinct_paths(first: Path, second: Path | None, message: str) -> None:
    if second is not None and first.resolve() == second.resolve():
        raise ValueError(message)


def _load_parser_factory(spec: str) -> Callable[[], Parser]:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("--parser must be MODULE:FACTORY")
    factory = getattr(import_module(module_name), attribute)
    if not callable(factory):
        raise TypeError(f"Parser factory {spec!r} is not callable")
    return factory


def _handle_twin(args: argparse.Namespace) -> int:
    from .twin import build_twin_bank, render_twin_html

    _ensure_distinct_paths(args.output, args.json_output, "HTML and JSON output paths must differ")
    bank = build_twin_bank(args.frames)
    _write_utf8(args.output, render_twin_html(bank))
    if args.json_output is not None:
        _write_utf8(args.json_output, json.dumps(bank, indent=2, allow_nan=False))
    print(
        json.dumps(
            {
                "scenarios": len(bank["scenarios"]),
                "frames": args.frames,
                "html": str(args.output),
                "json": str(args.json_output) if args.json_output is not None else None,
            }
        )
    )
    return 0


def _handle_profile(args: argparse.Namespace) -> int:
    from .profile_report import render_profile_html
    from .profiler import run_profile

    _ensure_distinct_paths(args.output, args.html, "JSON and HTML output paths must differ")
    report = run_profile(args.records, args.repeats, args.warmups)
    _write_utf8(args.output, json.dumps(report, indent=2, allow_nan=False))
    if args.html is not None:
        _write_utf8(args.html, render_profile_html(report))
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "checks": len(report["gate"]["checks"]),
                "workloads": len(report["workloads"]),
                "report": str(args.output),
                "html": str(args.html) if args.html is not None else None,
            }
        )
    )
    return 0 if report["passed"] else 1


def _collect_demo_reports() -> list[ReplayResult]:
    reports: list[ReplayResult] = []
    folder = resources.files("uart_fixture_kit").joinpath("fixtures")
    for resource in sorted(folder.iterdir(), key=lambda item: item.name):
        if not resource.name.endswith(".json"):
            continue
        with resources.as_file(resource) as path:
            fixture = load_fixture(path)
            factory: Callable[[], Parser] = (
                BinaryParser if path.name.startswith("binary_") else JsonLineParser
            )
            reports.append(replay(fixture, factory()))
    return reports


def _emit_replay_reports(reports: Sequence[ReplayResult], *, as_json: bool) -> int:
    passed = all(result.passed for result in reports)
    if as_json:
        print(
            json.dumps(
                {
                    "passed": passed,
                    "fixtures": [
                        {
                            "name": result.name,
                            "passed": result.passed,
                            "records": result.records,
                            "expected": result.expected,
                            "diff": result.diff,
                        }
                        for result in reports
                    ],
                },
                indent=2,
                allow_nan=False,
            )
        )
    else:
        for result in reports:
            print(f"{'PASS' if result.passed else 'FAIL'} {result.name}")
            if not result.passed:
                print(result.diff)
        print(f"{sum(result.passed for result in reports)}/{len(reports)} fixtures passed")
    return 0 if passed else 1


def _emit_error(error: Exception, *, as_json: bool) -> int:
    logger.debug("CLI command failed: %s", error)
    if as_json:
        print(json.dumps({"passed": False, "error": str(error)}, allow_nan=False))
    else:
        print(f"ERROR: {error}", file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "twin":
            return _handle_twin(args)
        if args.command == "profile":
            return _handle_profile(args)
        if args.command == "demo":
            reports = _collect_demo_reports()
        else:
            factory = _load_parser_factory(args.parser)
            reports = [replay(load_fixture(args.fixture), factory())]
    except (ValueError, TypeError, OSError, ImportError, AttributeError) as error:
        return _emit_error(error, as_json=bool(args.json))
    return _emit_replay_reports(reports, as_json=bool(args.json))


if __name__ == "__main__":
    raise SystemExit(main())
