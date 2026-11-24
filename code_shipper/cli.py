"""Command line entry point.

Phase 1 exposes only what exists: `doctor` and `version`. The rest of the CLI (ship, watch, review,
approvals, repos, tick) arrives with the phases that build them, so the help text never advertises a
command that does nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="code-shipper",
        description="Review, fix and get pull requests ready to merge, with any agent harness.",
    )
    parser.add_argument("--version", action="version", version=f"code-shipper {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser(
        "doctor", help="report VERIFIED / UNVERIFIED / UNSUPPORTED per backend"
    )
    doctor.add_argument("--json", action="store_true", help="machine-readable output")
    doctor.add_argument(
        "--no-contract",
        action="store_true",
        help="skip the hermes one-shot contract probe (about a minute) and report it unmeasured",
    )
    doctor.add_argument(
        "--require",
        metavar="BACKEND",
        help="exit non-zero unless this backend is VERIFIED",
    )
    return parser


def cmd_doctor(args: argparse.Namespace) -> int:
    from .doctor import VERIFIED, diagnose

    report = diagnose(contract=not args.no_contract)
    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print(report.render())

    if args.require:
        status = report.backend_status(args.require)
        if status != VERIFIED:
            print(
                f"\n{args.require} is {status}, not VERIFIED; refusing.",
                file=sys.stderr,
            )
            return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return cmd_doctor(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
