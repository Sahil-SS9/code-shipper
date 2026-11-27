"""N7: the observe-mode calibration report. `code-shipper approvals report --repo X --since 14d`.

For pull requests merged in the window, it compares what the current approvals policy would have said with
what the human reviewers actually did, and sorts each one into four cases:

    policy satisfied + reviewers asked for changes   -> too loose. Fix these first.
    policy not satisfied + reviewers approved        -> maybe too strict
    policy satisfied + reviewers approved            -> agree
    policy not satisfied + reviewers asked for changes / no review -> agree

"Policy satisfied" means: with the approvals that were actually given, the current rules would have been met.
It uses the policy at the base branch's *current* tip, because the question is whether today's rules are
calibrated, not what yesterday's said. Read-only: nothing is posted, labelled or stored.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from .evaluate import Facts, evaluate

TOO_LOOSE = "policy satisfied, reviewers asked for changes"
TOO_STRICT = "policy not satisfied, reviewers approved"
AGREE_APPROVE = "agree: approved"
AGREE_HOLD = "agree: held"
ORDER = (TOO_LOOSE, TOO_STRICT, AGREE_APPROVE, AGREE_HOLD)
MAX_PRS = 100


def parse_since(text: str, *, now: float | None = None) -> float:
    """'14d', '2w', '36h' into an epoch cut-off. Anything else is refused, not guessed."""
    match = re.fullmatch(r"\s*(\d{1,4})\s*([hdw])\s*", str(text or ""))
    if not match:
        raise ValueError(f"--since must look like 14d, 2w or 36h, not {text!r}")
    amount, unit = int(match.group(1)), match.group(2)
    seconds = amount * {"h": 3600, "d": 86400, "w": 7 * 86400}[unit]
    return (time.time() if now is None else now) - seconds


def human_outcome(reviews: list[Any]) -> tuple[str, tuple[str, ...]]:
    """What people did, from each reviewer's latest decisive review. ('changes'|'approved'|'none', approvers)."""
    latest: dict[str, str] = {}
    for review in reviews:
        state = str(getattr(review, "state", "") or "").upper()
        if state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[str(getattr(review, "author", ""))] = state
    approvers = tuple(sorted(login for login, state in latest.items() if state == "APPROVED"))
    if any(state == "CHANGES_REQUESTED" for state in latest.values()):
        return "changes", approvers
    return ("approved" if approvers else "none"), approvers


def classify(satisfied: bool, human: str) -> str:
    if satisfied and human == "changes":
        return TOO_LOOSE
    if not satisfied and human == "approved":
        return TOO_STRICT
    if satisfied and human == "approved":
        return AGREE_APPROVE
    return AGREE_HOLD


@dataclass
class ReportRow:
    number: int
    title: str
    case: str
    policy: str
    human: str
    approvers: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"number": self.number, "title": self.title, "case": self.case, "policy": self.policy,
                "human": self.human, "approvers": list(self.approvers)}


@dataclass
class Report:
    rows: list[ReportRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    truncated: bool = False

    def counts(self) -> dict[str, int]:
        return {case: sum(1 for row in self.rows if row.case == case) for case in ORDER}

    def as_dict(self) -> dict[str, Any]:
        return {"counts": self.counts(), "rows": [row.as_dict() for row in self.rows],
                "errors": self.errors, "truncated": self.truncated}

    def render(self) -> str:
        lines = [f"{len(self.rows)} merged pull request(s)"]
        if self.truncated:
            lines[0] += f" (first {MAX_PRS} only)"
        for case, count in self.counts().items():
            lines.append(f"  {count:>3}  {case}" + ("   <- fix these first" if case == TOO_LOOSE and count else ""))
        for case in (TOO_LOOSE, TOO_STRICT):
            for row in (r for r in self.rows if r.case == case):
                lines.append(f"  #{row.number} {row.title[:60]} — {row.policy}; reviewers: {row.human}")
        for problem in self.errors:
            lines.append(f"  could not assess: {problem}")
        lines.append("Read-only: nothing was posted. Advisory. Code-Shipper never approves or merges.")
        return "\n".join(lines)


def build_report(policy: Any, merged: list[dict[str, Any]]) -> Report:
    """`merged` items: {"pr": PullRequest, "files": [...], "reviews": [...]} — or {"pr", "error"}."""
    report = Report(truncated=len(merged) > MAX_PRS)
    for entry in merged[:MAX_PRS]:
        pr = entry["pr"]
        if entry.get("error"):
            report.errors.append(f"#{pr.number}: {entry['error']}")
            continue
        human, approvers = human_outcome(entry.get("reviews") or [])
        files = tuple(entry.get("files") or ())
        verdict = evaluate(policy, Facts(changed_files=files, author=pr.author, labels=tuple(pr.labels or ()),
                                         draft=False, approvals=approvers))
        if not verdict.evaluated:
            report.errors.append(f"#{pr.number}: {verdict.summary()}")
            continue
        # Observe-only rules are what N7 is for: judge them as if they were enforced.
        satisfied = all(r.satisfied for r in verdict.rules if r.applies) if verdict.rules else verdict.satisfied
        report.rows.append(ReportRow(pr.number, pr.title, classify(satisfied, human),
                                     "satisfied" if satisfied else f"not satisfied ({verdict.summary()})",
                                     {"changes": "asked for changes", "approved": "approved",
                                      "none": "no review"}[human], approvers))
    return report
