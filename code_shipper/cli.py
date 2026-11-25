"""Command line entry point.

Phase 1 exposes only what exists: `doctor` and `version`. The rest of the CLI (ship, watch, review,
approvals, repos, tick) arrives with the phases that build them, so the help text never advertises a
command that does nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any
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
    review.add_argument(
        "--profile",
        default="standard",
        choices=["quiet", "standard", "assertive"],
        help="how much to report: quiet hides nits, assertive lowers the bar (default: standard)",
    )
    review.add_argument(
        "--trigger",
        default="manual",
        choices=["manual", "open", "on-push"],
        help="what caused this review; on-push and open review only commits since the last complete "
             "review (default: manual)",
    )
    review.add_argument(
        "--state-dir",
        default="",
        help="where review state and the cache live (default: ~/.local/state/code-shipper)",
    )
    review.add_argument(
        "--with-ci",
        action="store_true",
        help="read the failing check's log and annotation paths as hints (C10)",
    )
    review.add_argument(
        "--max-reviews",
        type=int,
        default=20,
        help="stop reviewing this PR after this many reviews, with a stated reason (default: 20)",
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

    ship = sub.add_parser("ship", help="take a local change to an open pull request")
    ship.add_argument("--repo", required=True, metavar="OWNER/NAME", help="repository slug")
    ship.add_argument("--branch", required=True, help="branch to create and push")
    ship.add_argument("--base", required=True, help="base branch for the pull request")
    ship.add_argument("--title", required=True, help="pull request title")
    ship.add_argument("--body", default="", help="pull request body")
    ship.add_argument(
        "--add",
        action="append",
        default=[],
        metavar="PATH",
        help="a file to include, relative to --workdir (repeatable)",
    )
    ship.add_argument("--workdir", required=True, help="local checkout to commit in")
    ship.add_argument("--author-name", required=True, help="commit author name")
    ship.add_argument("--author-email", required=True, help="commit author email")
    ship.add_argument("--draft", action="store_true", help="open the pull request as a draft")
    ship.add_argument("--dry-run", action="store_true", help="plan and scan only; write nothing")
    ship.add_argument(
        "--i-authorise-publish",
        nargs="?",
        const="i-authorise-publish",
        default=None,
        help="record authorisation to publish (required in a non-interactive run)",
    )
    ship.add_argument("--json", action="store_true", help="machine-readable output")

    poll = sub.add_parser("poll", help="watch for new heads and review each one")
    poll.add_argument("pr", type=int, help="pull request number")
    poll.add_argument("--repo", required=True, metavar="OWNER/NAME", help="repository slug")
    poll.add_argument("--interval", type=float, default=30.0, help="seconds between polls")
    poll.add_argument("--minutes", type=float, default=0.0, help="stop after this long (0 = no limit)")
    poll.add_argument("--rounds", type=int, default=0, help="stop after this many rounds")
    poll.add_argument(
        "--stop-label",
        default="code-shipper:stop",
        help="stop when this label is absent or a stop command is present",
    )
    poll.add_argument("--dry-run", action="store_true", help="review without posting")
    poll.add_argument("--state-dir", default="", help="where review state lives")
    poll.add_argument("--json", action="store_true", help="machine-readable output")

    ui = sub.add_parser("ship-ui", help="render the TUI board for a state (no network)")
    ui.add_argument(
        "state",
        nargs="?",
        default="findings",
        choices=["empty", "reviewing", "findings", "incomplete", "approvals-blocked",
                 "approvals-satisfied", "observe", "paused", "stopped", "error"],
        help="which state to draw",
    )
    ui.add_argument("--width", type=int, default=113, help="columns")
    ui.add_argument("--height", type=int, default=30, help="rows")

    page = sub.add_parser("ship-page", help="write the Desktop page for a state as self-contained HTML")
    page.add_argument(
        "state",
        nargs="?",
        default="findings",
        choices=["empty", "reviewing", "findings", "incomplete", "approvals-blocked",
                 "approvals-satisfied", "observe", "paused", "stopped", "error"],
        help="which state to draw",
    )
    page.add_argument("--out", required=True, help="where to write the HTML")

    config = sub.add_parser("config", help="inspect configuration")
    config.add_argument(
        "action",
        choices=["effective"],
        help="effective: the merged policy and where each value came from",
    )
    config.add_argument("--repo", required=True, metavar="OWNER/NAME", help="repository slug")
    config.add_argument(
        "--central",
        default="",
        help="path to the shared central policy this repo inherits (optional)",
    )
    config.add_argument(
        "--local",
        default="",
        help="path to a local policy file (default: code-shipper.yml in the repo root)",
    )
    config.add_argument("--json", action="store_true", help="machine-readable output")

    lessons = sub.add_parser("lessons", help="inspect or withdraw review lessons")
    lessons.add_argument("action", choices=["list", "show", "withdraw"])
    lessons.add_argument("lesson_id", nargs="?", default="", help="lesson id, for show and withdraw")
    lessons.add_argument("--state-dir", default="", help="where review state lives")
    lessons.add_argument("--all", action="store_true", help="include withdrawn lessons")
    lessons.add_argument("--json", action="store_true", help="machine-readable output")
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
    from .review.framing import ci_annotations, hint_block
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

    # C10: a failing check's log and annotation paths are *hints*, not evidence. They come from code
    # the PR author wrote, so they are fetched only to point the review at the right files, and the
    # prompt says so. Opt-in, because logs can be large and noisy and a routine review does not need
    # them.
    log_tail = ""
    hint_paths: list[str] = []
    if args.with_ci:
        try:
            log_tail = host.failing_log(args.repo, pr.head_sha)
            hint_paths = host.annotations(args.repo, pr.head_sha)
        except Exception as exc:  # noqa: BLE001 - a missing log must not fail a review
            print(f"note: CI hints unavailable ({exc}); continuing without them", file=sys.stderr)
        if log_tail:
            log_tail = hint_block(ci_annotations(log_tail) or hint_paths) + "\n" + log_tail

    from .review.cache import ReviewCache
    from .review.service import ReviewRequest, run as run_service
    from .review.state import ReviewStore

    state_root = Path(args.state_dir).expanduser() if args.state_dir else _state_dir()
    store = ReviewStore(state_root)
    cache = ReviewCache(state_root)

    # An on-push or on-open review covers only commits since the last complete review. A manual
    # review deliberately covers the whole PR: a human asking for a review wants all of it.
    since_diff = None
    existing = store.load(args.repo, args.pr)
    if args.trigger in ("on-push", "open") and existing.has_baseline:
        try:
            since_diff = host.compare(args.repo, existing.last_reviewed_head, pr.head_sha)
        except Exception:  # noqa: BLE001 - fall back to a full review rather than skip commits
            since_diff = None

    result = run_service(
        ReviewRequest(
            repo=args.repo,
            pr=args.pr,
            head_sha=pr.head_sha,
            diff=diff,
            since_diff=since_diff,
            level=args.level,
            profile=args.profile,
            trigger=args.trigger,
            intent=intent_block(pr.title, pr.body),
            log_tail=log_tail,
            workdir=args.workdir,
            env=scrubbed_env(),
        ),
        harness=harness,
        store=store,
        cache=cache,
        max_reviews_per_pr=args.max_reviews,
    )
    outcome = result.outcome

    outcome_dict = result.as_dict()
    outcome_dict["pr"] = args.pr
    outcome_dict["repo"] = args.repo
    outcome_dict["head_sha"] = pr.head_sha

    if args.json:
        print(json.dumps(outcome_dict, indent=2))
    else:
        print(f"PR #{args.pr} {pr.title} ({pr.head_sha[:7]}) level={args.level} "
              f"profile={args.profile}")
        if result.cached:
            print("cached: this head was already reviewed, so no model call was made")
        if result.incremental and result.scope is not None:
            print(f"incremental: {result.scope.summary()}")
        if result.paused:
            print(f"PAUSED: {result.pause_reason}")
        if result.ignored_summary:
            print(result.ignored_summary)
        print(f"lenses: {', '.join(outcome.lenses_run) or 'none'}")
        print(f"findings: {outcome.raw_findings} raw, {outcome.confirmed} confirmed, "
              f"{len(outcome.findings)} to post")
        if result.risk.get("summary"):
            print(result.risk["summary"])
        if result.effort.get("triggered"):
            print(f"low-effort signals: {len(result.effort.get('signals', []))} "
                  f"(label: {result.effort.get('label') or 'none'})")
        if result.union_important:
            print(f"important open across the whole PR: {len(result.union_important)}")
        if not outcome.complete:
            print("INCOMPLETE: " + "; ".join(outcome.errors))
        for finding in outcome.findings:
            print(f"\n  [{finding.severity}] {finding.path}:{finding.line} {finding.title}")
            if finding.body:
                print(f"    {finding.body[:300]}")
        if outcome.plan and outcome.plan.summary_line():
            print(f"\n{outcome.plan.summary_line()}")
    return 0 if outcome.complete and not result.paused else 1


def _state_dir() -> Path:
    """Where review state and the cache live. Not under ~/.hermes: that path is refused by the
    container spec, and a state dir that cannot be mounted next to a run is the wrong dir."""
    import os

    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "code-shipper"


def cmd_lessons(args: argparse.Namespace) -> int:
    """`lessons list|show|withdraw`: inspect what the reviewer has learnt, and unlearn it.

    Read-only except `withdraw`. A lesson that cannot be unlearnt is a permanent bias, so removal is a
    first-class command rather than an edit to a JSON file.
    """
    from .review.memory import Store, list_lessons

    root = Path(args.state_dir).expanduser() if args.state_dir else _state_dir()
    store = Store(root)

    if args.action == "list":
        rows = list_lessons(store, include_withdrawn=args.all)
        if args.json:
            print(json.dumps(rows, indent=2, sort_keys=True))
        elif not rows:
            print("no lessons" + ("" if args.all else " (withdrawn lessons are hidden; use --all)"))
        else:
            for row in rows:
                print(f"{row['id']}  [{row['disposition']}]  {row['action']}")
        return 0

    if not args.lesson_id:
        print(f"lessons {args.action}: a lesson id is required", file=sys.stderr)
        return 2

    if args.action == "show":
        lesson = store.get(args.lesson_id)
        if lesson is None:
            print(f"no lesson {args.lesson_id}", file=sys.stderr)
            return 1
        print(json.dumps(lesson.as_dict(), indent=2, sort_keys=True) if args.json
              else f"{lesson.id}\n  cue:      {lesson.cue}\n  action:   {lesson.action}\n"
                   f"  evidence: {lesson.evidence}\n  source:   {lesson.source}\n"
                   f"  state:    {lesson.disposition}")
        return 0

    # withdraw
    if not store.withdraw(args.lesson_id):
        print(f"no lesson {args.lesson_id}", file=sys.stderr)
        return 1
    print(f"withdrew {args.lesson_id}; it will not affect the next review")
    return 0


def cmd_ship(args: argparse.Namespace) -> int:
    """`ship`: plan, scan, require authorisation, then branch/commit/push/PR.

    The interactive path asks for confirmation when a terminal is attached. With no terminal it
    refuses unless `--i-authorise-publish` was passed — it never blocks waiting for input that cannot
    arrive.
    """
    from . import secrets as secrets_mod
    from .ship import Authorisation, Plan, ShipRefused, make_plan, ship

    workdir = Path(args.workdir).expanduser()
    if not workdir.is_dir():
        print(f"workdir {workdir} is not a directory", file=sys.stderr)
        return 2

    files: dict[str, str] = {}
    for relative in args.add:
        candidate = workdir / relative
        try:
            files[relative] = candidate.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"cannot read {relative}: {exc}", file=sys.stderr)
            return 2

    try:
        plan = make_plan(
            repo=args.repo,
            branch=args.branch,
            base=args.base,
            title=args.title,
            body=args.body,
            files=files,
            author_name=args.author_name,
            author_email=args.author_email,
            draft=args.draft,
        )
    except ShipRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    print(plan.render())
    print()

    # Authorisation: explicit flag, or an interactive confirmation when a terminal is attached. Never
    # a default, and never a silent wait in a non-TTY run.
    value = args.i_authorise_publish
    if value is None and not args.dry_run:
        if sys.stdin.isatty():
            try:
                answer = input("Publish this? Type 'yes' to continue: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""
            value = "yes" if answer == "yes" else "no"
        else:
            print(
                "refused: no terminal is attached and no authorisation was recorded. Pass "
                "--i-authorise-publish to record one.",
                file=sys.stderr,
            )
            return 2

    authorisation = Authorisation.from_flag(value, by=args.author_name or "cli")

    token = _read_token_quietly()
    broker = None
    if token and not args.dry_run:
        from .broker.roles import Broker

        broker = Broker(token=token, repo=args.repo)

    try:
        result = ship(
            plan,
            workdir=workdir,
            authorisation=authorisation,
            broker=broker,
            dry_run=args.dry_run,
            log=lambda message: print(f"  {message}"),
        )
    except secrets_mod.SecretFound as found:
        # Already reported by the flow; exit non-zero so a script cannot mistake it for success.
        return 3
    except ShipRefused as exc:
        print(f"refused [{exc.code}]: {exc.detail}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"ship failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        print()
        print(f"branch: {result.branch}")
        print(f"commit: {result.commit[:12] if result.commit else '(none)'}")
        print(f"pushed: {result.pushed}")
        print(f"pr:     {('#' + str(result.pr)) if result.pr else '(none)'}")
        print()
        print("Nothing was merged and nothing was approved. A human merges.")
    return 0


def cmd_poll(args: argparse.Namespace) -> int:
    """`poll`: review every new head until the deadline, a stop, or the round count.

    Stop is checked at the top of each round, before the head is read: a stop honoured only between
    rounds is honoured late, and one honoured at the end is not honoured at all.
    """
    from .hosts.github import GitHubHost, split_slug
    from .review.cache import ReviewCache
    from .review.service import ReviewRequest, run as run_service
    from .review.state import ReviewStore
    from .harness.adapters import HermesHarness
    from .steward import commands as commands_mod
    from .steward.poll import poll
    from .steward.watch import read_stop_signals

    token = _read_token_quietly()
    if not token:
        print("no GitHub token found; cannot poll.", file=sys.stderr)
        return 2

    owner, name = split_slug(args.repo)
    host = GitHubHost(token)
    repo = f"{owner}/{name}"
    state_root = Path(args.state_dir).expanduser() if args.state_dir else _state_dir()
    store = ReviewStore(state_root)
    cache = ReviewCache(state_root)
    harness = HermesHarness()

    def current_head() -> str:
        return host.get_pr(repo, args.pr).head_sha

    def is_stopped() -> bool:
        # Signals are read fresh each round, never remembered: a label removed a second ago or a stop
        # comment posted a second ago must take effect in this round, not the next one.
        comments = host.list_comments(repo, args.pr)
        parsed = [
            commands_mod.evaluate(comment, lambda login: host.permission_of(repo, login))
            for comment in comments
        ]
        labels = {str(label) for label in host.list_labels(repo, args.pr)}
        stopped, why = commands_mod.should_stop(
            commands=parsed, labelled=args.stop_label in labels
        )
        if stopped:
            print(f"  stop: {why}")
        return stopped

    def on_new_head(old: str, new: str) -> None:
        pr = host.get_pr(repo, args.pr)
        existing = store.load(repo, args.pr)
        since_diff = None
        if existing.has_baseline:
            try:
                since_diff = host.compare(repo, existing.last_reviewed_head, new)
            except Exception:  # noqa: BLE001 - fall back to a full review rather than skip commits
                since_diff = None
        result = run_service(
            ReviewRequest(
                repo=repo,
                pr=args.pr,
                head_sha=new,
                diff=host.get_diff(repo, args.pr),
                since_diff=since_diff,
                trigger="on-push",
                intent=intent_block(pr.title, pr.body),
                workdir=None,
                env=scrubbed_env(),
            ),
            harness=HermesHarness(),
            store=store,
            cache=cache,
        )
        if not args.dry_run:
            _post_review(token=token, repo=repo, pr=args.pr, result=result)
        print(f"  reviewed: {len(result.outcome.findings)} finding(s), "
              f"{result.model_calls} model call(s)"
              + (" (cached)" if result.cached else "")
              + (" (incremental)" if result.incremental else ""))
        if result.risk.get("summary"):
            print(f"  {result.risk['summary']}")

        # Approvals re-evaluation happens in the same round as the review, from the same head, so the
        # comment and the review can never describe different commits. A refusal here stops the
        # approvals half, not the review: the review already happened and its result is true.
        try:
            _reevaluate_approvals(host=host, repo=repo, pr=args.pr, token=token,
                                  dry_run=args.dry_run)
        except ApprovalRefused as exc:
            print(f"  approvals refused: {exc}", file=sys.stderr)

    outcome = poll(
        current_head=current_head,
        is_stopped=is_stopped,
        on_new_head=on_new_head,
        on_round=lambda round_: print(
            f"round {round_.number}: {round_.reason} (head {round_.head[:8] or 'unknown'})"
        ),
        interval=args.interval,
        minutes=args.minutes,
        max_rounds=args.rounds,
    )

    if args.json:
        print(json.dumps(outcome.as_dict(), indent=2, sort_keys=True))
    else:
        print()
        print(f"stopped: {outcome.stopped_reason or 'end of rounds'}")
        print(f"rounds: {len(outcome.rounds)}, reviews: {outcome.reviewed_count}")
    return 0


def _post_review(*, token: str, repo: str, pr: int, result: Any) -> int:
    """Post a review through the broker, and record what was posted.

    COMMENT only. The broker refuses any other event for the reviewer role before a request is built,
    so this cannot become an approval by a caller passing a different event.
    """
    from .broker.roles import Broker, BrokerRefused, BrokerError

    comments = []
    for finding in result.outcome.findings:
        comments.append({
            "path": finding.path,
            "line": finding.line,
            "body": f"**{finding.title}** ({finding.severity})\n\n{finding.body}",
        })
    if not comments:
        print("  no findings to post")
        return 0

    broker = Broker(token=token, repo=repo)
    try:
        posted = broker.post_review(
            role="reviewer", pr=pr, head_sha=result.state.last_reviewed_head or "",
            body=result.outcome.summary() if hasattr(result.outcome, "summary") else "Review",
            event="COMMENT", comments=comments,
        )
    except BrokerRefused as exc:
        print(f"  review refused by the broker: {exc}", file=sys.stderr)
        return 2
    except BrokerError as exc:
        print(f"  review failed: {exc}", file=sys.stderr)
        return 2
    print(f"  posted review {posted.get('id')} ({len(comments)} inline)")
    return 0


class ApprovalRefused(Exception):
    """The approvals policy cannot be located or applied. Refused, never defaulted."""


def _reevaluate_approvals(
    *, host: Any, repo: str, pr: int, token: str, dry_run: bool = False
) -> dict:
    """Re-evaluate approvals for this PR and create or update the one status comment.

    One comment per PR, edited in place. A push that re-posts would bury the current state under its
    own history, so an unchanged body is not posted at all.
    """
    from .approvals.comment import find_existing, render, should_update
    from .approvals.evaluate import Facts, evaluate
    from .approvals.policy import PolicyError, parse
    from .approvals.shared import merge
    from .broker.roles import Broker, BrokerError, BrokerRefused

    # The policy is read at the PR's BASE COMMIT, never from the PR, never from the repository default,
    # and never from a branch *name*.
    #
    # Three separate reasons, all measured or required:
    # - The PR must not govern itself (T-ADV-01).
    # - The repository default is the wrong base for any PR against a non-default branch. This was a
    #   real defect: PR #25's policy lived on demo/base and was reported absent.
    # - A branch name is not a fixed reference. Reading "the branch" can return a policy that changed
    #   after the head being evaluated, so the verdict and the code it describes can drift apart. The
    #   exact-head principle applies to the policy as much as to the diff.
    #
    # A missing base_ref is refused rather than defaulted: `base_ref or default_branch` silently
    # restored the very bug above whenever the field was absent, and a fallback that reintroduces a
    # fixed defect under a slightly different input is not a fallback, it is the defect.
    pr_obj = host.get_pr(repo, pr)
    base_ref = str(getattr(pr_obj, "base_ref", "") or "").strip()
    if not base_ref:
        raise ApprovalRefused(
            "this pull request reports no base branch, so the approvals policy cannot be located. "
            "Refusing rather than reading the repository default, which may not be the base this "
            "pull request targets."
        )
    base_sha = str(getattr(pr_obj, "base_sha", "") or "").strip()
    if not base_sha:
        raise ApprovalRefused(
            f"this pull request reports no base commit for {base_ref}, so the policy cannot be read at "
            "a fixed reference. Refusing rather than reading a branch name, which can move."
        )
    # `pr.base.sha` is GitHub's merge base, not the tip of the base branch: the API returned
    # 2e9e11ad for a branch whose tip was 514cc54b. Reading the policy there would apply a stale
    # policy — the tip's policy had already changed from count-2-with-one-approver to something valid.
    # So resolve the branch to its current commit once, and read the policy at that fixed SHA.
    try:
        base_sha = host.resolve_ref(repo, base_ref) or base_sha
    except Exception:  # noqa: BLE001 - fall back to the reported sha rather than a branch name
        pass

    central_text = ""
    local_text = ""
    try:
        fetched = host.read_file(repo, base_sha, ".code-shipper/approvals.yml")
        central_text = fetched.decode() if fetched else ""
    except Exception:  # noqa: BLE001 - absent central config is normal
        central_text = ""
    try:
        fetched = host.read_file(repo, base_sha, "code-shipper.yml")
        local_text = fetched.decode() if fetched else ""
    except Exception:  # noqa: BLE001
        local_text = ""

    try:
        central = parse(central_text, source="central") if central_text else None
        local = parse(local_text, source="base") if local_text else None
    except PolicyError as exc:
        print(f"  approvals: the policy is invalid: {exc}")
        return {"state": "error", "reason": str(exc)}

    merged = merge(central, local, repo=repo)
    for refusal in merged.refused:
        print(f"  approvals: {refusal}")

    files = tuple(diff_file.path for diff_file in host.get_diff(repo, pr).files)
    facts = Facts(
        changed_files=files,
        author=pr_obj.author,
        labels=tuple(pr_obj.labels or ()),
        draft=bool(pr_obj.draft),
        approvals=tuple(host.approvals(repo, pr)),
        edits_policy="code-shipper.yml" in files or ".code-shipper/approvals.yml" in files,
    )
    verdict = evaluate(merged.policy, facts)
    comment = render(verdict, facts=facts, policy=merged.policy)
    print(f"  approvals: {verdict.summary()}")

    if dry_run:
        print("  approvals: dry run, nothing posted")
        return comment.as_dict()

    existing = find_existing(host.list_comments(repo, pr))
    broker = Broker(token=token, repo=repo)
    try:
        if existing is None:
            broker.post_comment(pr=pr, body=comment.body, role="user")
            print("  approvals: created the status comment")
        elif should_update(getattr(existing, "body", ""), comment):
            broker.update_comment(pr=pr, comment_id=int(getattr(existing, "id")),
                                  body=comment.body, role="user")
            print("  approvals: updated the status comment in place")
        else:
            print("  approvals: unchanged, nothing posted")
    except (BrokerRefused, BrokerError) as exc:
        print(f"  approvals: could not post: {exc}", file=sys.stderr)
    return comment.as_dict()


def cmd_ship_ui(args: argparse.Namespace) -> int:
    """Draw the TUI board for a state. Pure rendering: no network, no credentials, no writes."""
    from .surfaces import fixtures, tui

    projection = fixtures.ALL[args.state]()
    for line in tui.render(projection, width=args.width, height=args.height):
        print(line)
    return 0


def cmd_ship_page(args: argparse.Namespace) -> int:
    """Write the Desktop page for a state as one self-contained HTML file."""
    from .surfaces import desktop, fixtures

    projection = fixtures.ALL[args.state]()
    html = desktop.render(projection)
    target = Path(args.out).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    print(f"wrote {target} ({len(html)} bytes)")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    """`config effective`: the merged policy, with the source of every value (C13, D15d).

    Read-only. It prints what *would* be applied, which is the point: a tighten-only rule that refuses
    an override is invisible unless you can see the effective result and why a value stayed central.
    """
    from .approvals.policy import PolicyError, parse
    from .approvals.shared import merge

    def read(path_text: str, label: str) -> object | None:
        if not path_text:
            return None
        try:
            return parse(Path(path_text).expanduser().read_text(encoding="utf-8"), source=label)
        except OSError as exc:
            print(f"could not read the {label} policy: {exc}", file=sys.stderr)
            raise SystemExit(2) from None

    central = read(args.central, "central")
    local_path = args.local
    if not local_path:
        token = _read_token_quietly()
        if token:
            from .hosts.github import GitHubHost

            host = GitHubHost(token)
            try:
                raw = host.read_file(args.repo, _base_branch(host, args.repo), "code-shipper.yml")
            except Exception:  # noqa: BLE001 - no config in the repo is normal
                raw = b""
            if raw:
                try:
                    local = parse(raw.decode("utf-8", "replace"), source="repo")
                except PolicyError as exc:
                    print(f"the repo's policy is invalid: {exc}", file=sys.stderr)
                    return 2
            else:
                local = None
        else:
            local = None
    else:
        local = read(local_path, "repo")

    merged = merge(central, local, repo=args.repo)  # type: ignore[arg-type]
    if args.json:
        print(json.dumps(merged.as_dict(), indent=2, sort_keys=True))
    else:
        print(effective(merged))
    return 0


def _base_branch(host, repo: str) -> str:
    try:
        return host.default_branch(repo)
    except Exception:  # noqa: BLE001
        return "main"


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
    if args.command == "config":
        return cmd_config(args)
    if args.command == "ship-ui":
        return cmd_ship_ui(args)
    if args.command == "ship-page":
        return cmd_ship_page(args)
    if args.command == "ship":
        return cmd_ship(args)
    if args.command == "poll":
        return cmd_poll(args)
    if args.command == "lessons":
        return cmd_lessons(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
