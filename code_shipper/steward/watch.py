"""The watch loop: `code-shipper watch <pr>` (FR-S1–S5, T-SCN-01..04).

A loop that can be stopped, and that stops itself before it does something irreversible.

Order within a round, and the order is the safety property:

1. **Check cancellation and the stop conditions before doing anything.** A stop command or a removed
   label ends the loop at the top of the round, not after the current fix — FR-S3 says "after the
   current round" for a mid-fix stop, so the round is allowed to finish but nothing new starts.
2. **Check the budget** — our clock, rounds, pushes.
3. Classify, fix, check scope, push. Every one of those consults the cancel token again.
4. Record the round.

The loop never merges, never approves, and has no code path that could: the broker has no such
operation. "Unattended" here means "will fix and push to the PR head branch within a budget", not
"will finish the job by merging it".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from ..budget import BudgetExceeded, RunBudget
from . import commands as commands_mod
from .attempts import AttemptLog, problem_key
from .cancel import CancelToken, Cancelled
from .classify import WorkKind, actionable
from .fix import FixReceipt, run_fix
from .policy import ScopePolicy


class Host(Protocol):
    """The read-and-comment surface the loop needs. Writes go through the broker, never here."""

    def list_comments(self, repo: str, number: int, since: str | None = None) -> list: ...
    def permission_of(self, repo: str, login: str) -> str: ...
    def get_pr(self, repo: str, number: int) -> Any: ...
    def list_labels(self, repo: str, number: int) -> list[str]: ...


@dataclass
class Round:
    index: int
    verdict: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"round": self.index, "verdict": self.verdict, **self.detail}


@dataclass
class WatchReceipt:
    """What the loop did, and why it stopped."""

    started: float = field(default_factory=time.time)
    rounds: list[Round] = field(default_factory=list)
    stopped_because: str = ""
    stopped_by: str = ""
    cancelled: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    label_added: bool = False
    label_removed: bool = False
    pushed: list[str] = field(default_factory=list)
    model_calls_after_cancel: int = 0
    # N2: problems handed back after MAX_ATTEMPTS_PER_PROBLEM tries, each with the attempts made.
    needs_you: list[dict[str, Any]] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return round(time.time() - self.started, 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rounds": [r.as_dict() for r in self.rounds],
            "stopped_because": self.stopped_because,
            "stopped_by": self.stopped_by,
            "cancelled": self.cancelled,
            "budget": self.budget,
            "label_added": self.label_added,
            "label_removed": self.label_removed,
            "pushed": self.pushed,
            "model_calls_after_cancel": self.model_calls_after_cancel,
            "needs_you": self.needs_you,
            "duration_s": self.duration_s,
        }


def watch(
    *,
    repo: str,
    pr_number: int,
    host: Host,
    worker,
    policy: ScopePolicy,
    budget: RunBudget,
    repo_dir: Path,
    token: str | None = None,
    cancel: CancelToken | None = None,
    max_rounds: int = 3,
    sleep_s: float = 0.0,
    staging_root: Path | None = None,
    clock: Callable[[], float] = time.monotonic,
    label_present: Callable[[], bool] | None = None,
    after_round: Callable[[int], None] | None = None,
    attempts: AttemptLog | None = None,
) -> WatchReceipt:
    """Run rounds until the work is done, the budget is spent, or someone stops it.

    `attempts` carries the per-problem count across watches. Omitting it does not switch the cap off: an
    in-memory log is used, so three tries per problem holds within this watch regardless.
    """
    cancel = cancel or CancelToken()
    attempts = attempts if attempts is not None else AttemptLog()
    receipt = WatchReceipt()
    seen: set[str] = set()

    for index in range(1, max_rounds + 1):
        # 1. Stop conditions, checked before any work in the round.
        try:
            cancel.check()
        except Cancelled as exc:
            receipt.stopped_because = f"cancelled: {exc.reason}"
            receipt.cancelled = exc.detail | {"token": cancel.as_dict()}
            break

        pr = host.get_pr(repo, pr_number)
        comments = host.list_comments(repo, pr_number)
        parsed = [
            commands_mod.evaluate(comment, lambda login: host.permission_of(repo, login))
            for comment in comments
            if str(getattr(comment, "id", "")) not in seen
        ]
        labelled = label_present() if label_present is not None else True
        must_stop, why = commands_mod.should_stop(commands=parsed, labelled=labelled)
        if must_stop:
            receipt.stopped_because = why
            stop_command = commands_mod.latest(parsed)
            receipt.stopped_by = stop_command.author if stop_command else ""
            break

        seen |= {str(getattr(c, "id", "")) for c in comments}

        # A fresh instruction replaces the standing one for this round.
        instruction = commands_mod.latest(parsed)
        if instruction is not None and instruction.kind == commands_mod.CommandKind.INSTRUCTION:
            receipt.rounds.append(
                Round(index, "instruction", {"text": instruction.text, "author": instruction.author})
            )
            if sleep_s:
                time.sleep(sleep_s)
            continue

        # 2. Budget, before the round is allowed to start.
        try:
            budget.check_clock()
        except BudgetExceeded as exc:
            receipt.stopped_because = f"budget: {exc}"
            break

        # 3. Do the work.
        try:
            from .classify import classify

            items = classify(
                pr=pr,
                mergeable_state=(getattr(pr, "mergeable_state", "") or "").lower(),
                checks=[],
                comments=[c for c in comments if getattr(c, "body", "")],
                findings=[],
                changed_files=list(getattr(pr, "changed_files", []) or []),
            )
            # N2: a problem already tried MAX_ATTEMPTS_PER_PROBLEM times is handed back, not worked.
            workable = []
            for item in actionable(items):
                key = problem_key(item)
                if attempts.exhausted(key):
                    if not any(entry["key"] == key for entry in receipt.needs_you):
                        receipt.needs_you.append({"key": key, "reason": item.reason,
                                                  "attempts": attempts.attempts(key),
                                                  "reported": attempts.reported(key)})
                else:
                    workable.append(item)
            if receipt.needs_you and not workable:
                receipt.stopped_because = (
                    f"needs you: {len(receipt.needs_you)} problem(s) still failing after "
                    f"{attempts.limit} attempts each"
                )
                break
            result: FixReceipt = run_fix(
                items=workable,
                repo_dir=repo_dir,
                base_sha=getattr(pr, "base_sha", ""),
                head_sha=getattr(pr, "head_sha", ""),
                head_branch=getattr(pr, "head_ref", ""),
                worker=worker,
                policy=policy,
                budget=budget,
                token=token,
                staging_root=staging_root,
                push=token is not None,
                cancel=cancel,
            )
            round_record = Round(index, result.verdict, {"ok": result.ok, "stage": result.stage})
            if workable and result.verdict not in ("nothing_to_do", "cancelled", "in_scope_not_pushed"):
                detail = "; ".join(result.problems)[:300] if result.problems else ""
                attempts.record(workable[0], verdict=result.verdict, detail=detail)
            if result.ok and result.push.get("ok"):
                receipt.pushed.append(result.push.get("new_sha", ""))
            receipt.rounds.append(round_record)
        except Cancelled as exc:
            # The fix was killed mid-flight. Nothing was pushed: run_fix pushes only after the scope
            # check, and cancellation is checked before it.
            receipt.rounds.append(Round(index, "cancelled_mid_fix", {"reason": exc.reason,
                                                                    "detail": exc.detail}))
            receipt.stopped_because = f"cancelled mid-fix: {exc.reason}"
            receipt.cancelled = exc.detail
            break

        if result.verdict in ("nothing_to_do", "in_scope_not_pushed"):
            receipt.stopped_because = f"nothing further to do ({result.verdict})"
            break

        if after_round is not None:
            after_round(index)
        if sleep_s:
            time.sleep(sleep_s)
    else:
        receipt.stopped_because = f"reached max_rounds ({max_rounds})"

    receipt.budget = budget.as_dict()
    if not receipt.cancelled:
        receipt.cancelled = cancel.as_dict()
    return receipt
