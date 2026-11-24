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

    review = sub.add_parser("review", help="review a pull request")
    review.add_argument("pr", type=int, help="pull request number")
    review.add_argument(
        "--repo",
        required=True,
        metavar="OWNER/NAME",
        help="repository slug on the code host",
    )
    review.add_argument(
        "--level",
        default="standard",
        choices=["quick", "standard", "deep"],
        help="review depth (default: standard)",
    )
    review.add_argument(
        "--dry-run",
        action="store_true",
        help="run the review and print findings without posting anything",
    )
    review.add_argument("--json", action="store_true", help="machine-readable output")
    review.add_argument(
        "--workdir",
        default=".",
        help="directory the agent runs in (default: the current directory)",
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


def cmd_review(args: argparse.Namespace) -> int:
    """Run a review. With --dry-run nothing is posted and no token is required."""
    import os

    from .harness.adapters import HermesHarness
    from .harness.adapters import scrubbed_env
    from .hosts.github import GitHubHost
    from .review.engine import review as run_review

    if not args.dry_run:
        print(
            "posting a review is not implemented yet (P2-B); use --dry-run",
            file=sys.stderr,
        )
        return 2

    token = os.environ.get("CODE_SHIPPER_GITHUB_TOKEN") or _read_token_quietly()
    if not token:
        print(
            "no GitHub token found. Set CODE_SHIPPER_GITHUB_TOKEN, or place one at "
            "~/.config/code-shipper/tokens/fixer. Dry runs still need read access.",
            file=sys.stderr,
        )
        return 2

    host = GitHubHost(token)
    try:
        diff = host.get_diff(args.repo, args.pr)
        pr = host.get_pr(args.repo, args.pr)
    except Exception as exc:  # noqa: BLE001 - a read failure is a message, not a traceback
        print(f"could not read PR #{args.pr} in {args.repo}: {exc}", file=sys.stderr)
        return 2

    harness = HermesHarness()
    ok, reason = harness.available()
    if not ok:
        print(f"harness unavailable: {reason}", file=sys.stderr)
        return 2

    outcome = run_review(
        diff=diff,
        harness=harness,
        level=args.level,
        workdir=args.workdir,
        env=scrubbed_env(),
    )
    outcome_dict = outcome.as_dict()
    outcome_dict["pr"] = args.pr
    outcome_dict["repo"] = args.repo
    outcome_dict["head_sha"] = pr.head_sha

    if args.json:
        print(json.dumps(outcome_dict, indent=2))
    else:
        print(f"PR #{args.pr} {pr.title} ({pr.head_sha[:7]}) level={args.level}")
        print(f"lenses: {', '.join(outcome.lenses_run)}")
        print(f"findings: {outcome.raw_findings} raw, {outcome.confirmed} confirmed, "
              f"{len(outcome.findings)} to post")
        if not outcome.complete:
            print("INCOMPLETE: " + "; ".join(outcome.errors))
        for finding in outcome.findings:
            print(f"\n  [{finding.severity}] {finding.path}:{finding.line} {finding.title}")
            if finding.body:
                print(f"    {finding.body[:300]}")
        if outcome.plan and outcome.plan.summary_line():
            print(f"\n{outcome.plan.summary_line()}")
    return 0 if outcome.complete else 1


def _read_token_quietly() -> str:
    from pathlib import Path

    path = Path.home() / ".config" / "code-shipper" / "tokens" / "fixer"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "review":
        return cmd_review(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
