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

    watch = sub.add_parser("watch", help="watch a pull request and fix it within a budget")
    watch.add_argument("pr", type=int, help="pull request number")
    watch.add_argument("--repo", required=True, metavar="OWNER/NAME", help="repository slug")
    watch.add_argument(
        "--rounds", type=int, default=3, help="maximum fix rounds (default: 3)"
    )
    watch.add_argument(
        "--minutes", type=float, default=15.0, help="wall-clock limit in minutes (default: 15)"
    )
    watch.add_argument("--dry-run", action="store_true",
                       help="run the loop but never push (no token needed)")
    watch.add_argument("--json", action="store_true", help="machine-readable output")
    watch.add_argument(
        "--label",
        default="code-shipper-watching",
        help="label that must stay on the PR for the loop to continue",
    )

    stop = sub.add_parser("stop", help="tell a running watch loop to stop")
    stop.add_argument("pr", type=int, help="pull request number")
    stop.add_argument("--repo", required=True, metavar="OWNER/NAME", help="repository slug")
    stop.add_argument("--json", action="store_true", help="machine-readable output")
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
    from .review.engine import intent_block
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
        intent=intent_block(pr.title, pr.body),
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


def cmd_watch(args: argparse.Namespace) -> int:
    """Watch a PR: classify, fix, check scope, push — within a budget, and stoppable.

    Nothing here merges or approves. `--dry-run` runs the identical loop but passes no token, so no
    push can happen; it exercises the same code path rather than a separate one.
    """
    import tempfile

    from .budget import RunBudget
    from .hosts.github import GitHubHost
    from .steward.policy import ScopePolicy
    from .steward.watch import watch as run_watch

    token = _read_token_quietly()
    if not token:
        print(
            "no GitHub token found at ~/.config/code-shipper/tokens/fixer. The loop needs read "
            "access even for a dry run.",
            file=sys.stderr,
        )
        return 2

    host = GitHubHost(token)
    try:
        pr = host.get_pr(args.repo, args.pr)
    except Exception as exc:  # noqa: BLE001 - a read failure is a message, not a traceback
        print(f"could not read PR #{args.pr} in {args.repo}: {exc}", file=sys.stderr)
        return 2

    # The policy comes from the repository's own config, never from a hard-coded path list.
    policy = ScopePolicy.from_config(_read_scope_config(args.repo) or {})

    with tempfile.TemporaryDirectory(prefix="cs-watch-") as workdir:
        receipt = run_watch(
            repo=args.repo,
            pr_number=args.pr,
            host=host,
            worker=_container_worker(),
            policy=policy,
            budget=RunBudget(
                fix_rounds=args.rounds,
                wall_clock_seconds=int(max(1.0, args.minutes) * 60),
            ),
            repo_dir=Path(workdir),
            token=None if args.dry_run else token,
            label_present=lambda: args.label in host.list_labels(args.repo, args.pr),
        )

    payload = receipt.as_dict()
    payload["pr"] = args.pr
    payload["repo"] = args.repo
    payload["dry_run"] = bool(args.dry_run)

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"PR #{args.pr} {pr.title}")
        for record in receipt.rounds:
            print(f"  round {record.index}: {record.verdict}")
        print(f"stopped: {receipt.stopped_because or 'reason not recorded'}")
        if receipt.pushed:
            print(f"pushed: {', '.join(sha[:7] for sha in receipt.pushed)}")
        else:
            print("pushed: nothing")
        if args.dry_run:
            print("dry run: no token was used, so no push could happen")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    """Stop a running watch loop.

    Two mechanisms exist and both are honoured by the loop: `@code-shipper stop` from a writer, and
    removing the watching label. This command does the second, because it is the one an operator can
    do without leaving a comment — and it is a write, so it goes through the broker's `user` role,
    which is where every write belongs.
    """
    from .broker.roles import Broker, BrokerRefused
    from .hosts.github import split_slug

    token = _read_token_quietly()
    if not token:
        print("no GitHub token found; cannot change labels.", file=sys.stderr)
        return 2

    owner, name = split_slug(args.repo)
    broker = Broker(token=token, repo=f"{owner}/{name}")
    try:
        broker.remove_label(pr=args.pr, label="code-shipper-watching", role="user")
    except BrokerRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"could not remove the label from PR #{args.pr}: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"pr": args.pr, "repo": args.repo, "label_removed": True}))
    else:
        print(f"PR #{args.pr}: removed code-shipper-watching; a running loop stops after this round")
    return 0


def _read_scope_config(repo: str) -> dict:
    """The repository's scope policy.

    Read from `code-shipper.yml` on the default branch when the host provides one. No YAML parser is
    bundled (K1 / ADR 0004: standard library only), so the supported shape is a flat `scope:` block of
    lists, which covers what the policy needs. Anything else is refused rather than half-parsed — a
    config file silently ignored is a scope rule silently absent.
    """
    from .config import read_scope_block
    from .hosts.github import GitHubHost

    token = _read_token_quietly()
    if not token:
        return {}
    host = GitHubHost(token)
    try:
        raw = host.read_file(repo, host.default_branch(repo), "code-shipper.yml")
    except Exception:  # noqa: BLE001 - no config is not an error
        return {}
    if not raw:
        return {}
    return read_scope_block(raw.decode("utf-8", "replace"))


def _container_worker():
    """The contained worker, wired the same way a real run uses it."""
    from .steward.contained import ContainedWorker

    return ContainedWorker()


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
    if args.command == "watch":
        return cmd_watch(args)
    if args.command == "stop":
        return cmd_stop(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
