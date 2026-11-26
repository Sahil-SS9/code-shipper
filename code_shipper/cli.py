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


# The exit-code contract, in one place: --help and the README both quote these.
EXIT_CODES_HELP = """exit codes:
  0  clean         the review completed and found nothing to fix
  1  findings      the review completed and found something to fix
  2  error         the run failed; nothing was reviewed
  3  incomplete    the review did not finish; this is not a clean result

  Incomplete is deliberately distinct from error: an incomplete review can be retried, but must
  never be treated as clean."""


# Subparsers inherit nothing from the parent parser, so the flag is repeated on each one as it is
# created. Defined at module scope so no subparser can be built before it exists.
_NO_ABBREV = {"allow_abbrev": False}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="code-shipper",
        description="Review, fix and get pull requests ready to merge, with any agent harness.",
        epilog=EXIT_CODES_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        # Abbreviations are off everywhere. `--pr` expanded to `--profile` on `review`, so
        # `review --repo X --pr 25` failed with "argument --profile: invalid choice: '25'" — an error
        # naming a flag the caller never typed.
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"code-shipper {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser(
        "doctor", help="report VERIFIED / UNVERIFIED / UNSUPPORTED per backend", **_NO_ABBREV
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

    review = sub.add_parser("review", help="review a pull request", **_NO_ABBREV)
    # Both forms: the positional is the original interface, `--pr` matches every other subcommand so a
    # caller can use one shape throughout.
    review.add_argument("pr", type=int, nargs="?", help="pull request number")
    review.add_argument("--pr", type=int, dest="pr_flag", default=None,
                        help="pull request number (same as the positional)")
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
        "--memory-mode",
        default="shadow",
        choices=["off", "shadow", "apply"],
        help="review memory: off, shadow (record only, default), or apply (lessons reach the prompt)",
    )
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

    watch = sub.add_parser("watch", help="watch a pull request and fix it within a budget", **_NO_ABBREV)
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

    stop = sub.add_parser("stop", help="tell a running watch loop to stop", **_NO_ABBREV)
    stop.add_argument("pr", type=int, help="pull request number")
    stop.add_argument("--repo", required=True, metavar="OWNER/NAME", help="repository slug")
    stop.add_argument("--json", action="store_true", help="machine-readable output")

    ship = sub.add_parser("ship", help="take a local change to an open pull request", **_NO_ABBREV)
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

    poll = sub.add_parser("poll", help="watch for new heads and review each one", **_NO_ABBREV)
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

    ship_ui = sub.add_parser("ship-ui", help="draw the terminal board for a pull request", **_NO_ABBREV)
    ship_ui.add_argument(
        "state",
        nargs="?",
        default="auto",
        choices=["auto"] + ["empty", "reviewing", "findings", "incomplete", "approvals-blocked",
                 "approvals-satisfied", "observe", "paused", "stopped", "error"],
        help="a state name draws DEMO data (requires --demo); 'auto' draws a real review",
    )
    ship_ui.add_argument("--repo", default="", help="owner/name; defaults to the most recent review")
    ship_ui.add_argument("--pr", type=int, default=None, help="pull request number")
    ship_ui.add_argument("--state-dir", default="", help="where review state lives")
    ship_ui.add_argument("--demo", action="store_true",
                        help="draw sample data instead of a real review (labelled as demo data)")
    ship_ui.add_argument("--width", type=int, default=113, help="columns")
    ship_ui.add_argument("--height", type=int, default=30, help="rows")

    ui = sub.add_parser("ui", help="draw the terminal board for a pull request", **_NO_ABBREV)
    ui.add_argument(
        "state",
        nargs="?",
        default="auto",
        choices=["auto"] + ["empty", "reviewing", "findings", "incomplete", "approvals-blocked",
                 "approvals-satisfied", "observe", "paused", "stopped", "error"],
        help="a state name draws DEMO data (requires --demo); 'auto' draws a real review",
    )
    ui.add_argument("--repo", default="", help="owner/name; defaults to the most recent review")
    ui.add_argument("--pr", type=int, default=None, help="pull request number")
    ui.add_argument("--state-dir", default="", help="where review state lives")
    ui.add_argument("--demo", action="store_true",
                        help="draw sample data instead of a real review (labelled as demo data)")
    ui.add_argument("--width", type=int, default=124,
                    help="columns (under 70 collapses to one column)")
    ui.add_argument("--height", type=int, default=34, help="rows")
    ui.add_argument("--json", action="store_true", help="the same facts as data")

    fix = sub.add_parser("fix", help="run one bounded fix round on a pull request", **_NO_ABBREV)
    fix.add_argument("pr", type=int, help="pull request number")
    fix.add_argument("--repo", required=True, help="owner/name")
    fix.add_argument("--branch", required=True, help="the pull request's head branch")
    fix.add_argument("--state-dir", default="", help="where the review state lives")
    fix.add_argument("--dry-run", action="store_true",
                     help="plan and check the budget, then stop before writing anything")
    fix.add_argument("--json", action="store_true", help="the result as data")

    status = sub.add_parser("status", help="list everything watched, with how often it is checked", **_NO_ABBREV)
    status.add_argument("--repo", default="", help="repository to report on")
    status.add_argument("--check-every-minutes", type=int, default=2,
                        help="the polling interval actually in use")
    status.add_argument("--json", action="store_true", help="the same facts as data")
    status.add_argument("--state-dir", default="", help="where review state lives")
    status.add_argument("--demo", action="store_true",
                        help="show the sample board instead of stored state (labelled as demo data)")

    page = sub.add_parser("ship-page", help="write the Desktop page for a real review", **_NO_ABBREV)
    page.add_argument(
        "state",
        nargs="?",
        default="auto",
        choices=["auto"] + ["empty", "reviewing", "findings", "incomplete", "approvals-blocked",
                            "approvals-satisfied", "observe", "paused", "stopped", "error"],
        help="a state name draws DEMO data (requires --demo); 'auto' draws a real review",
    )
    page.add_argument("--repo", default="", help="owner/name; defaults to the most recent review")
    page.add_argument("--pr", type=int, default=None, help="pull request number")
    page.add_argument("--state-dir", default="", help="where review state lives")
    page.add_argument("--demo", action="store_true", help="draw sample data (labelled as demo data)")
    page.add_argument("--out", required=True, help="where to write the HTML")

    config = sub.add_parser("config", help="inspect configuration", **_NO_ABBREV)
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

    lessons = sub.add_parser("lessons", help="inspect or withdraw review lessons", **_NO_ABBREV)
    lessons.add_argument("action", choices=["list", "show", "withdraw", "add"])
    lessons.add_argument("lesson_id", nargs="?", default="", help="lesson id, for show and withdraw")
    lessons.add_argument("--state-dir", default="", help="where review state lives")
    lessons.add_argument("--all", action="store_true", help="include withdrawn lessons")
    lessons.add_argument("--cue", default="", help="for `add`: what this lesson is about")
    lessons.add_argument("--action", dest="lesson_action", default="",
                         help="for `add`: what the reviewer should do differently")
    lessons.add_argument("--evidence", default="", help="for `add`: the adjudication that justifies it")
    lessons.add_argument("--author", default="", help="for `add`: who supplied the feedback")
    lessons.add_argument("--permission", default="none",
                         help="for `add`: the author's permission on this repository")
    lessons.add_argument("--mode", default="", help="off | shadow | apply (default: shadow)")
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
            title=pr.title or "",
            diff=diff,
            since_diff=since_diff,
            level=args.level,
            profile=args.profile,
            trigger=args.trigger,
            intent=intent_block(pr.title, pr.body),
            log_tail=log_tail,
            workdir=args.workdir,
            env=scrubbed_env(),
            # Wired here rather than left to the engine: the state dir the lessons were written to is
            # the one the review must read. Reading a different dir is how a feature looks wired and
            # does nothing.
            lessons_dir=state_root,
            memory_mode=args.memory_mode,
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
        if args.dry_run:
            print("dry run: nothing was posted and no review state was stored")
        else:
            # Accurate, rather than a promise: this command reviews and stores; it does not post.
            # `poll` and `watch` are the posting path, and they post the same review this computed.
            print("not posted: `review` stores the result but does not comment on GitHub. "
                  "Run `code-shipper watch` or `poll` to post it.")
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
        if not args.dry_run:
            print(f"\nsee it: code-shipper ui --repo {args.repo} --pr {args.pr}")
    return _review_exit_code(outcome)


def _review_exit_code(outcome) -> int:
    """The documented contract, which `review` had its own wrong version of.

    It returned `0 if complete and not paused else 1`, so a review with two important findings exited 0 —
    a clean result, to any caller reading the exit code — and an incomplete review exited 1, which means
    "findings" and invites a fix loop over a review that never finished.

       0  clean
       1  findings
       2  error
       3  incomplete

    An incomplete review outranks findings: a partial review's findings are real, but the caller must not
    treat the review as done either. Matches `cli_view.exit_code`, which is what the surfaces use.
    """
    from .surfaces.cli_view import EXIT_CLEAN, EXIT_ERROR, EXIT_FINDINGS, EXIT_INCOMPLETE

    if outcome.errors and not outcome.complete:
        return EXIT_ERROR
    if not outcome.complete:
        return EXIT_INCOMPLETE
    findings = outcome.findings
    if findings:
        return EXIT_FINDINGS
    return EXIT_CLEAN


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
    from .review.memory import DEFAULT_MODE, Feedback, Store, learn, list_lessons

    root = Path(args.state_dir).expanduser() if args.state_dir else _state_dir()
    store = Store(root)

    if args.action == "add":
        # The write path D15c promised. Without it every adjudicated false confirm is thrown away, and
        # the reviewer repeats the same scope creep on the next PR.
        evidence = " ".join((args.evidence or "").split())
        if not (args.cue or "").strip() or not (args.lesson_action or "").strip():
            print("lessons add: --cue and --action are required", file=sys.stderr)
            return 2
        if not evidence:
            # The gate, kept from the module: a lesson with no adjudication behind it is an opinion, and
            # an opinion that changes every future review is worse than no lesson.
            print(
                "lessons add: --evidence is required. A lesson is only allowed to change future reviews "
                "if it records what was adjudicated and why; without that it is an opinion.",
                file=sys.stderr,
            )
            return 2

        feedback = Feedback(
            finding_fingerprint=args.lesson_id or "",
            disposition="agreed",
            author=args.author,
            # `body` is the evidence column the lesson stores, so the adjudication goes here — not the
            # cue, which is already carried separately and would leave the lesson unevidenced.
            body=evidence,
            permission=args.permission,
            target_cue=args.cue,
            target_action=args.lesson_action,
        )
        result = learn(store, feedback, mode=args.mode or DEFAULT_MODE)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        elif result.get("accepted"):
            lesson = result.get("lesson") or {}
            print(f"recorded lesson {lesson.get('id', '?')}: {args.lesson_action}")
            print(f"  evidence: {evidence[:200]}")
            if (args.mode or DEFAULT_MODE) == "shadow":
                print("  mode: shadow — recorded, not applied to prompts yet")
        else:
            print(f"refused: {result.get('reason', 'unknown reason')}", file=sys.stderr)
        return 0 if result.get("accepted") else 1

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
                title=pr.title or "",
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
    """Draw the terminal board. The same function as `ui`: two names, one behaviour.

    `ship-ui` was the Phase 5b name and `ui` the Phase 5c one. Keeping both is a courtesy to anyone who
    learned the first; keeping two implementations would have been the bug — this one kept drawing
    fixtures long after `ui` was rewired.
    """
    # `ship-ui` has no --json flag, so `cmd_ui`'s attribute read would raise AttributeError. Default it
    # rather than adding a flag the older name never had.
    if not hasattr(args, "json"):
        args.json = False
    return cmd_ui(args)


def cmd_fix(args: argparse.Namespace) -> int:
    """One bounded fix round.

    Deliberately thin: the round itself lives in `steward.fix`, which already enforces scope, the budget
    and cancellation. This command resolves the inputs and reports the verdict; it re-implements none of
    those rules, because a second copy of a budget check is a budget check that can disagree.
    """
    import json as _json

    from .hosts.github import GitHubHost
    from .surfaces import cli_view, fixtures
    from .steward.policy import ScopePolicy

    if not args.state_dir:
        print("--state-dir is required: a fix round needs the review state it is fixing",
              file=sys.stderr)
        return cli_view.EXIT_ERROR

    if args.dry_run:
        print("plan: one bounded fix round")
        print(f"  repo   {args.repo}")
        print(f"  branch {args.branch}")
        print("  scope  from the policy file, not from this command")
        print("  stop   before any write (--dry-run)")
        if args.json:
            print(_json.dumps({"dry_run": True, "pr": args.pr, "branch": args.branch}, indent=2))
        return cli_view.EXIT_CLEAN

    # A live round needs credentials, a workspace and a container; none of those are implied by a flag.
    print(
        "a live fix round needs a workspace and a container; run it through `code-shipper watch`,\n"
        "which owns the round, the budget and the stop signal. `code-shipper fix --dry-run` shows the plan.",
        file=sys.stderr,
    )
    return cli_view.EXIT_ERROR


def _projection_for(args: argparse.Namespace):
    """The projection a surface should draw: stored state, or the sample board under --demo.

    One place decides this, so no surface can quietly keep reading fixtures. `--state` is only meaningful
    with `--demo`, and saying so is better than silently ignoring it.
    """
    from .surfaces import fixtures, loader

    demo = bool(getattr(args, "demo", False))
    state_name = getattr(args, "state", None)
    repo = getattr(args, "repo", "") or ""
    pr = getattr(args, "pr", None)

    if state_name and state_name != "auto":
        # A named state is a design-review screen. Treating it as real would need a PR to name too.
        if not demo and (not repo or not pr):
            print(
                "a named state draws sample data: pass --demo, or pass --repo and a pull request "
                "number to draw a real review",
                file=sys.stderr,
            )
            raise SystemExit(2)
        return fixtures.ALL[state_name](), demo

    if demo:
        return fixtures.status_board()[0], True

    if not repo or not pr:
        # No pull request named: draw the most recently reviewed one, which is what a person means by
        # "show me the board" when they have not said which.
        found = loader.discover(args.state_dir or None)
        if not found:
            print(
                "no reviews have been stored yet. Review one first:\n"
                "  code-shipper review <pr> --repo owner/name\n"
                "or see the sample board: --demo",
                file=sys.stderr,
            )
            raise SystemExit(2)
        repo, pr = found[0]
    return loader.load_projection(repo, int(pr),
                                  state_dir=args.state_dir or None), False


def cmd_ui(args: argparse.Namespace) -> int:
    """Draw the terminal board: the most recent real review, or a sample state under --demo."""
    from .surfaces import cli_view, tui

    projection, demo = _projection_for(args)
    if args.json:
        print(cli_view.render_json(projection))
        return 0
    lines = tui.render(projection, width=args.width, height=args.height)
    if demo:
        lines.insert(0, cli_view.demo_banner())
    for line in lines:
        print(line)
    return cli_view.exit_code(projection)


def cmd_status(args: argparse.Namespace) -> int:
    """List everything actually reviewed. The interval is stated because "watched" is meaningless
    without it.

    Real stored state, not fixtures. `--demo` is the only way to see the sample board, and it says so on
    every line of output — a demo that looks like a real board is how the surfaces came to be mistaken for
    working in the first place.
    """
    import json as _json

    from .surfaces import cli_view, fixtures, loader

    if args.demo:
        board = fixtures.status_board()
        rows = None
        colour = cli_view.colour_enabled()
        for line in cli_view.render_status(board, colour=colour, repo=args.repo or "demo",
                                           check_every_minutes=args.check_every_minutes,
                                           demo=True):
            print(line)
        return 0

    state_root = Path(args.state_dir).expanduser() if args.state_dir else loader.default_state_dir()
    # Only a run that is *live* counts as watched, and nothing on disk says that: `watch` holds the loop
    # in a process, not in the state file. So nothing is claimed. A paused pull request is paused, not
    # watched either — the loop stopped and is waiting for a person.
    watched = 0
    repo = args.repo or ""
    if repo:
        # One repository: list every pull request reviewed in it.
        board = [loader.load_projection(repo, pr, state_dir=state_root)
                 for repo_name, pr in loader.discover(state_root) if repo_name == repo]
    else:
        board = [loader.load_projection(name, pr, state_dir=state_root)
                 for name, pr in loader.discover(state_root)]

    if args.json:
        print(_json.dumps([
            {"pr": p.pr, "repo": p.repo, "title": p.title, "verdict": p.chip,
             "state": p.state, "reason": loader.short_reason(p),
             "exit_code": cli_view.exit_code(p)}
            for p in board
        ], indent=2, sort_keys=True))
        return 0

    colour = cli_view.colour_enabled()
    for line in cli_view.render_status(board, colour=colour, repo=repo or "all repositories",
                                       check_every_minutes=args.check_every_minutes,
                                       watched=watched):
        print(line)
    # The empty message belongs to the renderer, which knows the layout. Printing it here too made the
    # empty state say the same thing twice, in two slightly different wordings.
    return 0


def cmd_ship_page(args: argparse.Namespace) -> int:
    """Write the Desktop page for a real review, or a sample state under --demo."""
    from .surfaces import desktop

    projection, demo = _projection_for(args)
    html = desktop.render(projection, demo=demo)
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


def _resolve_pr_alias(args: argparse.Namespace) -> argparse.Namespace:
    """Fold `--pr` into `pr`, and refuse the cases that have no sensible answer.

    `review` takes the number positionally and everything else takes `--pr`. Accepting both on `review`
    lets a caller use one shape throughout; refusing two different numbers stops a typo from silently
    picking one.
    """
    flag = getattr(args, "pr_flag", None)
    if flag is None:
        return args
    positional = getattr(args, "pr", None)
    if positional is not None and positional != flag:
        raise SystemExit("give the pull request number once: positionally or with --pr, not both")
    args.pr = flag
    return args


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args = _resolve_pr_alias(args)
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
    if args.command == "fix":
        return cmd_fix(args)
    if args.command == "ui":
        return cmd_ui(args)
    if args.command == "status":
        return cmd_status(args)
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
