"""Run one fix: classify, prepare, fix, check scope, push (FR-S1–S5).

This module is the wiring, and it is deliberately thin. Every decision it makes is delegated:

* what to work on — `classify.py`
* where a fix may write, and whether a test was weakened — `policy.py`
* whether a push may happen at all — `fixloop.push`, which requires the head branch
* how much may be spent — `budget.py`

What lives here is the order of operations and the refusal to continue past a failed check. The order
is the security property: scope is checked **after** the fix and **before** the push, on the host, from
the files on disk rather than from anything the model said it did.

A run that fails any check stops. It does not retry with a wider scope, and it does not push what it
has.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from ..budget import BudgetExceeded, RunBudget
from ..contain import fixloop
from .classify import WorkItem, actionable
from .policy import ScopePolicy
from .workspace import Workspace, prepare


class FixWorker(Protocol):
    """Runs the harness inside the container. Returns what it changed, or raises."""

    def run(self, *, workspace: Workspace, item: WorkItem, prompt: str, budget: RunBudget) -> dict:
        ...


@dataclass
class FixReceipt:
    """Everything a reviewer needs to judge the run without trusting the summary."""

    ok: bool = False
    stage: str = ""
    verdict: str = ""
    item: dict[str, Any] = field(default_factory=dict)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    push: dict[str, Any] = field(default_factory=dict)
    workspace: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    scope_policy: dict[str, Any] = field(default_factory=dict)
    worker: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "stage": self.stage,
            "verdict": self.verdict,
            "item": self.item,
            "changed": self.changed,
            "removed": self.removed,
            "problems": self.problems,
            "push": self.push,
            "workspace": self.workspace,
            "budget": self.budget,
            "scope_policy": self.scope_policy,
            "worker": self.worker,
        }


def build_prompt(item: WorkItem, *, head_branch: str) -> str:
    """The instruction the worker gets. Says nothing about credentials, paths or the broker.

    Paths are not listed even when we know them: a worker told exactly which files to edit learns to
    expect that, and the next item may not have a known path. It gets the problem and the checkout.
    """
    lines = [
        "You are fixing one problem in a git checkout at /work.",
        "",
        f"The problem: {item.reason}",
    ]
    if item.detail.get("conclusion"):
        lines.append(f"The failing check concluded: {item.detail['conclusion']}")
    lines += [
        "",
        "Rules:",
        "- Edit files under /work only.",
        "- Fix the cause, not the symptom.",
        "- Do not weaken, skip or delete tests. If a test is wrong, explain why in your reply instead"
        " of changing it.",
        "- Do not add dependencies.",
        "- When you are done, stop. Do not commit, push or open anything.",
    ]
    return "\n".join(lines)


def run_fix(
    *,
    items: list[WorkItem],
    repo_dir: Path,
    base_sha: str,
    head_sha: str,
    head_branch: str,
    worker: FixWorker,
    policy: ScopePolicy,
    budget: RunBudget,
    token: str | None = None,
    staging_root: Path | None = None,
    commit_message: str = "",
    push: bool = True,
    keep: bool = False,
    remote: str = "",
    author: tuple[str, str] = ("Code-Shipper", "noreply@example.invalid"),
) -> FixReceipt:
    """One fix attempt, start to finish.

    ``push=False`` stops after the scope check, which is what a dry run wants. It still does all the
    work, so a dry run proves the same things about scope as a real one.
    """
    receipt = FixReceipt(
        scope_policy={
            "allowed": list(policy.allowed),
            "forbidden": list(policy.forbidden),
            "max_files": policy.max_files,
        }
    )

    work = actionable(items)
    if not work:
        receipt.stage = "classify"
        receipt.verdict = "nothing_to_do"
        receipt.item = items[0].as_dict() if items else {"kind": "none", "reason": ""}
        receipt.ok = True
        return receipt

    item = work[0]
    receipt.item = item.as_dict()

    try:
        budget.start_round()
    except BudgetExceeded as exc:
        receipt.stage = "budget"
        receipt.verdict = "budget_exhausted"
        receipt.problems = [str(exc)]
        receipt.budget = budget.as_dict()
        return receipt

    workspace: Workspace | None = None
    try:
        workspace = prepare(
            repo_dir,
            base_sha=base_sha,
            head_sha=head_sha,
            branch=head_branch,
            staging_root=staging_root,
            remote=remote,
            keep=keep,
        )
        receipt.workspace = workspace.describe()

        # Snapshot before the worker runs: the diff is derived from disk, not from what it claims.
        before = fixloop.snapshot(workspace.checkout)

        try:
            result = worker.run(
                workspace=workspace,
                item=item,
                prompt=build_prompt(item, head_branch=head_branch),
                budget=budget,
            )
            receipt.worker = dict(result or {})
        except BudgetExceeded as exc:
            receipt.stage = "worker"
            receipt.verdict = "budget_exhausted"
            receipt.problems = [str(exc)]
            receipt.budget = budget.as_dict()
            return receipt
        except Exception as exc:  # noqa: BLE001 - a worker failure must not escape as a crash
            receipt.stage = "worker"
            receipt.verdict = "worker_failed"
            receipt.problems = [f"{type(exc).__name__}: {exc}"]
            receipt.budget = budget.as_dict()
            return receipt

        verdict = fixloop.check_scope(
            before,
            workspace.checkout,
            original_root=workspace.pristine,
            policy=policy,
        )
        receipt.changed = verdict.changed
        receipt.removed = verdict.removed
        receipt.problems = verdict.problems

        if not verdict.ok:
            receipt.stage = "scope"
            receipt.verdict = "out_of_scope"
            receipt.budget = budget.as_dict()
            return receipt

        if not push:
            receipt.ok = True
            receipt.stage = "scope"
            receipt.verdict = "in_scope_not_pushed"
            receipt.budget = budget.as_dict()
            return receipt

        if token is None:
            receipt.stage = "push"
            receipt.verdict = "no_token"
            receipt.problems = ["a push was requested but no credential was supplied"]
            receipt.budget = budget.as_dict()
            return receipt

        try:
            budget.record_push()
        except BudgetExceeded as exc:
            receipt.stage = "budget"
            receipt.verdict = "budget_exhausted"
            receipt.problems = [str(exc)]
            receipt.budget = budget.as_dict()
            return receipt

        hooks = workspace.root / "empty-hooks"
        hooks.mkdir(exist_ok=True)
        receipt.push = fixloop.push(
            workspace.checkout,
            branch=head_branch,
            head_branch=head_branch,
            expected_sha=head_sha,
            commit_message=commit_message or f"Fix: {item.reason[:60]}",
            token=token,
            hookless_dir=hooks,
            author=author,
        )
        receipt.stage = "push"
        receipt.ok = bool(receipt.push.get("ok"))
        receipt.verdict = "pushed" if receipt.ok else f"push_failed:{receipt.push.get('stage', '')}"
        if not receipt.ok:
            receipt.problems = [str(receipt.push.get("detail", ""))]
        receipt.budget = budget.as_dict()
        return receipt
    finally:
        # Kept for inspection when the caller asks; a real run always cleans up.
        if workspace is not None and not getattr(workspace, "keep", False):
            workspace.cleanup()
