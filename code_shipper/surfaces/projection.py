"""One projection of review state, rendered by every surface (Phase 5b).

The rule that makes cross-surface parity testable: **surfaces hold no logic.** They draw a
`Projection` and nothing else. Every value — the verdict, the allowed actions, the budget, the
refusal — is decided here, once, so a TUI and a Desktop page cannot disagree.

Three design rules from the wireframes are enforced structurally rather than by convention:

**Merge is absent.** `ALLOWED_ACTIONS` has no merge member and `Projection.actions` cannot contain one.
A test asserts the merge string appears nowhere in the action vocabulary. The terminal state is "ready
for human merge" with one action, open the PR in a browser.

**Stop is not cancel, and neither is Escape.** `stop` settles the current round and stops watching;
`cancel` kills the current run. They are separate keys with separate labels, because one ambiguous
action that sometimes does both is how a user loses work.

**Colour is never the only channel.** Every state carries a text chip, so a monochrome terminal, a
screenshot in greyscale or a colour-blind reader loses nothing.
"""

from __future__ import annotations

from .mantra import NEVER_MERGES

from dataclasses import dataclass, field
from typing import Any

# The complete action vocabulary. Merge is not in it, and is not a disabled member of it: the surfaces
# draw from this set, so there is nothing to disable.
ALLOWED_ACTIONS = frozenset({
    "review", "fix", "stop", "cancel", "open-pr", "refresh", "explain-policy", "withdraw-lesson",
})

# Never reachable. Named so a test can assert its absence from the vocabulary above.
FORBIDDEN_ACTIONS = frozenset({"merge", "approve", "request-changes", "push-tag", "force-push"})

# States, with the text chip each renders. The chip is the accessible channel; the level selects a
# colour. A surface must never colour without also showing the chip.
# Verdict names, from the approved redesign. "Approved" never appears as a verdict: this tool does not
# approve, and a verdict word is the single place a reader is most likely to misread it.
STATES: dict[str, tuple[str, str]] = {
    # state            verdict              level
    "empty": ("", "muted"),
    "reviewing": ("Reviewing", "flight"),
    "findings": ("Needs fixes", "fail"),
    "incomplete": ("Incomplete, not a pass", "warn"),
    "approvals-blocked": ("Needs approvals", "warn"),
    "approvals-satisfied": ("Ready for you to merge", "pass"),
    "observe": ("Observe only", "muted"),
    "paused": ("Paused", "warn"),
    "stopped": ("Stopped", "muted"),
    "error": ("Error", "fail"),
}


@dataclass
class Budget:
    """Meters with units, and honesty about what is not measured."""

    fixed_rounds: int = 0
    max_rounds: int = 3
    pushes: int = 0
    max_pushes: int = 10
    elapsed_seconds: int = 0
    # Spend is unknown when the adapter does not meter it. It is never rendered as 0.
    spend_usd: float | None = None

    def rounds_text(self) -> str:
        return f"rounds {self.fixed_rounds}/{self.max_rounds}"

    def pushes_text(self) -> str:
        return f"pushes {self.pushes}/{self.max_pushes}"

    def spend_text(self) -> str:
        if self.spend_usd is None:
            return "spend unknown — harness doesn't meter"
        return f"spend ${self.spend_usd:.2f}"

    def clock_text(self) -> str:
        minutes, seconds = divmod(max(0, self.elapsed_seconds), 60)
        return f"{minutes:02d}:{seconds:02d}"


@dataclass
class Row:
    """One inbox row. Every row carries its reason in words — no invented health score."""

    repo: str
    pr: int
    title: str
    head: str
    state: str
    reason: str
    needs_human: bool = False
    branch: str = ""
    age_seconds: int = 0

    @property
    def chip(self) -> str:
        """The verdict name. Empty for the empty state, which has no verdict."""
        return STATES.get(self.state, ("Unknown", "muted"))[0]

    @property
    def level(self) -> str:
        return STATES.get(self.state, ("UNKNOWN", "muted"))[1]


@dataclass
class Finding:
    path: str
    line: int
    severity: str
    title: str
    evidence_kind: str = "diff"
    # What a reader needs to judge it in place, per the redesign: why it matters, the code with the hit
    # line marked, and the fix as a diff. `suggested_fix` is a (old, new) pair and is labelled as not
    # applied wherever it is drawn.
    why: str = ""
    code: tuple[tuple[str, int, str], ...] = ()
    suggested_fix: tuple[str, str] | None = None
    evidence: str = ""
    also_noted: tuple[str, ...] = ()


@dataclass
class Signal:
    """One of the four independent signals. Never compressed into a single badge."""

    name: str
    value: str
    ok: bool
    detail: str = ""


@dataclass
class Projection:
    """Everything a surface needs, and nothing a surface may add."""

    state: str = "empty"
    repo: str = ""
    pr: int = 0
    title: str = ""
    head: str = ""
    reviewed_head: str = ""
    verdict: str = ""
    rows: list[Row] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)
    refusal: str = ""
    error_headline: str = ""
    log: list[str] = field(default_factory=list)
    # The approved view switcher, in order. "Findings" is the default view.
    tabs: tuple[str, ...] = ("Findings", "What changed", "Approvals", "Activity")
    profile: str = "standard"
    runtime_owner: str = ""
    sync_age_seconds: int = 0
    # One sentence, one source (D16a). This used to read "Advisory. Code-Shipper never approves or
    # merges." while the Desktop said something else again.
    authority_note: str = NEVER_MERGES
    branch: str = ""
    base_ref: str = ""
    author: str = "kensei"
    who: str = "kensei · standard"
    approvers_missing: tuple[str, ...] = ()
    approvals_have: int = 0
    approvals_needed_count: int = 0
    approvals_needed: int = 0
    reviews_posted: int = 0
    review_cap: int = 0
    stopped_by: str = ""
    stopped_at_round: int = 0

    @property
    def chip(self) -> str:
        """The verdict name. Empty for the empty state, which has no verdict."""
        return STATES.get(self.state, ("Unknown", "muted"))[0]

    @property
    def level(self) -> str:
        return STATES.get(self.state, ("UNKNOWN", "muted"))[1]

    @property
    def head_pinned(self) -> bool:
        """Whether the verdict describes the head on screen. A stale verdict must say so."""
        return bool(self.head) and self.head == self.reviewed_head

    def next_bar(self) -> tuple[str, str, str, str]:
        """(headline, sub-line, primary_action, primary_label) for the NEXT bar.

        One sentence saying who acts and on what, one sub-line, one primary action. The copy is here
        rather than in a surface because a second copy of a sentence is how two surfaces start telling a
        reader different things.
        """
        count = len([f for f in self.findings if f.severity.lower() in ("important", "blocking")])
        if self.state == "reviewing":
            return ("Code-Shipper is reviewing the new commits",
                    "You can stop it; nothing is posted until the review finishes.", "stop", "Stop")
        if self.state == "findings":
            plural = "" if count == 1 else "s"
            return (f"You decide on {count} important finding{plural}",
                    "Fix them, or run one bounded fix round. You merge on GitHub.",
                    "fix", "Try a fix round")
        if self.state == "incomplete":
            return ("This review did not finish, so it proves nothing",
                    "Nothing was posted as an all-clear. Run it again.",
                    "review", "Review again")
        if self.state == "approvals-blocked":
            names = ", ".join(self.approvers_missing) if self.approvers_missing else "people"
            needed = self.approvals_needed or len(self.approvers_missing) or 1
            plural = "" if needed == 1 else "s"
            return (f"{needed} more approval{plural} needed on GitHub",
                    f"From: {names}. Code-Shipper cannot approve for them.",
                    "open-pr", "Open on GitHub")
        if self.state == "approvals-satisfied":
            # The agreed wording. "when you are ready" was a fifth phrasing of the same promise.
            return (f"Your rules are met. {NEVER_MERGES}",
                    "Code-Shipper has finished. The merge is yours to make.",
                    "open-pr", "Open on GitHub")
        if self.state == "observe":
            return ("Rules are in observe mode. Nothing is blocking",
                    "These rules report but do not enforce. Change them in the policy file.",
                    "explain-policy", "Explain the rule")
        if self.state == "paused":
            used = self.reviews_posted or 0
            cap = self.review_cap or 0
            return (f"Paused at the review cap ({used} of {cap})",
                    "Raise the cap, or stop watching this pull request.",
                    "refresh", "Resume")
        if self.state == "stopped":
            who = self.stopped_by or "a person"
            return (f"Stopped by {who} at round {self.stopped_at_round or 1}",
                    "Nothing runs until you restart it.",
                    "review", "Review again")
        if self.state == "error":
            # A short plain headline, then the cause as the detail. The old sub-line explained that the
            # message was a plain cause, which is the kind of sentence that tells a reader about the
            # writing instead of the problem.
            return (self.error_headline or "The review could not run",
                    self.refusal or "Nothing was checked.", "review", "Retry")
        if self.state == "empty":
            return ("No pull requests to watch yet",
                    "Initialise, then review or watch a pull request.", "refresh", "Refresh")
        return ("", "", "refresh", "Refresh")

    # States where no review completed. In these, "no findings" is not a clean result — it is the
    # absence of one, and saying it would be the single most misleading sentence a surface could print.
    NOT_COMPLETE_STATES = frozenset({"error", "reviewing", "paused", "stopped", "incomplete"})

    @property
    def review_completed(self) -> bool:
        """Whether a review actually finished. Derived from the state, which already encodes it."""
        return self.state not in self.NOT_COMPLETE_STATES

    def findings_placeholder(self) -> str:
        """The line a surface prints where findings would be.

        In a completed review with nothing to report it is a clean result. In an incomplete one the same
        words would claim the code was examined when it was not, so the line says what actually happened.
        """
        if self.review_completed:
            return "No findings at this head."
        return "The review didn't finish, so nothing was checked."

    def lanes(self) -> list[tuple[str, str, str]]:
        """The four steps under two labels: (step, icon_state, sub_line).

        `icon_state` is one of done / running / waiting / failed / notstarted / notneeded. The step that
        blocks progress is the one whose sub-line is not "done"; the surface highlights it.
        """
        review_state = {
            "empty": "notstarted", "reviewing": "running", "findings": "done",
            "incomplete": "failed", "approvals-blocked": "done",
            "approvals-satisfied": "done", "observe": "done", "paused": "waiting",
            "stopped": "notstarted", "error": "failed",
        }.get(self.state, "notstarted")

        # Fix is "not needed" when there is nothing to fix — not "done", which would imply work happened.
        important = [f for f in self.findings if f.severity.lower() in ("important", "blocking")]
        if not important and review_state == "done":
            fix_state, fix_sub = "notneeded", "not needed"
        elif important:
            fix_state, fix_sub = "waiting", f"{len(important)} to fix"
        else:
            fix_state, fix_sub = "notstarted", "waits on review"

        if self.state == "approvals-satisfied":
            ap_state, ap_sub = "done", f"{self.approvals_have} of {self.approvals_needed_count} met"
        elif self.state == "observe":
            ap_state, ap_sub = "notneeded", "observe only"
        elif self.state == "approvals-blocked":
            ap_state = "waiting"
            ap_sub = f"{self.approvals_have} of {self.approvals_needed_count}"
        else:
            ap_state, ap_sub = "notstarted", "after the review"

        merge_state, merge_sub = "notstarted", "yours to make"
        if self.state == "approvals-satisfied":
            merge_state, merge_sub = "waiting", "ready when you are"

        return [
            ("Review", review_state, "finished" if review_state == "done" else self.state),
            ("Fix", fix_state, fix_sub),
            ("Approvals", ap_state, ap_sub),
            ("You merge", merge_state, merge_sub),
        ]

    def inbox_groups(self) -> list[tuple[str, list[Row]]]:
        """Inbox grouping, in the order a reader needs it: needs you, then ready, then stopped."""
        groups: dict[str, list[Row]] = {"Needs you": [], "Ready for you to merge": [], "Stopped": []}
        for row in self.rows:
            if row.state in ("approvals-satisfied",):
                groups["Ready for you to merge"].append(row)
            elif row.state in ("stopped", "paused", "observe"):
                groups["Stopped"].append(row)
            else:
                groups["Needs you"].append(row)
        return [(name, rows) for name, rows in groups.items() if rows]

    def actions(self) -> list[str]:
        """The actions that are live in this state. Drawn from ALLOWED_ACTIONS only."""
        live: list[str] = []
        if self.state in ("reviewing", "paused"):
            live += ["stop", "refresh"]
        elif self.state in ("findings", "incomplete", "error"):
            live += ["fix", "review", "open-pr", "explain-policy"]
        elif self.state in ("approvals-blocked", "observe"):
            live += ["review", "open-pr", "explain-policy", "refresh"]
        elif self.state == "approvals-satisfied":
            live += ["open-pr", "refresh"]
        elif self.state == "stopped":
            live += ["review", "refresh"]
        elif self.state == "empty":
            live += ["refresh"]
        else:
            live += ["refresh"]
        if self.state == "reviewing":
            live.append("cancel")
        return [action for action in live if action in ALLOWED_ACTIONS]

    def context_line(self) -> str:
        """The persistent line above a board: connection, profile, owner, sync age."""
        return (
            f"connected · profile {self.profile} · owner {self.runtime_owner or 'unknown'} · "
            f"synced {self.sync_age_seconds}s ago"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "chip": self.chip,
            "level": self.level,
            "repo": self.repo,
            "pr": self.pr,
            "title": self.title,
            "head": self.head,
            "reviewed_head": self.reviewed_head,
            "head_pinned": self.head_pinned,
            "verdict": self.verdict,
            "actions": self.actions(),
            "signals": [s.__dict__ for s in self.signals],
            "findings": [f.__dict__ for f in self.findings],
            "budget": {
                "rounds": self.budget.rounds_text(),
                "pushes": self.budget.pushes_text(),
                "spend": self.budget.spend_text(),
                "clock": self.budget.clock_text(),
            },
            "refusal": self.refusal,
            "review_completed": self.review_completed,
            "findings_placeholder": self.findings_placeholder(),
            "rows": [r.__dict__ | {"chip": r.chip, "level": r.level} for r in self.rows],
        }


def build_from_review(
    *,
    repo: str,
    pr: int,
    title: str,
    head: str,
    reviewed_head: str,
    findings: list[Finding],
    complete: bool,
    approvals_state: str = "",
    approvals_detail: str = "",
    policy_state: str = "",
    ci_state: str = "",
    paused_reason: str = "",
    refusal: str = "",
    error_headline: str = "",
    error: str = "",
    budget: Budget | None = None,
    profile: str = "standard",
    runtime_owner: str = "",
) -> Projection:
    """Turn a review outcome into the one projection every surface draws.

    Order matters: an error outranks a pause, a pause outranks findings, findings outrank approvals,
    and an incomplete review outranks a clean one. Each of those is a case where a surface that chose
    differently would tell a user something untrue — for instance "needs approval" about a review that
    never finished, or "approved" about a review with open important findings.
    """
    projection = Projection(
        repo=repo, pr=pr, title=title, head=head, reviewed_head=reviewed_head,
        budget=budget or Budget(), profile=profile, runtime_owner=runtime_owner,
    )
    projection.signals = [
        Signal("review", projection.chip if not complete else "complete", complete,
               "the review finished" if complete else "the review did not finish"),
        Signal("checks", ci_state or "unknown", ci_state in ("passing", "success", "green"),
               "continuous integration"),
        Signal("policy", policy_state or "unknown", policy_state in ("satisfied", "observe"),
               "approvals policy"),
        Signal("approval", approvals_state or "unknown",
               approvals_state == "satisfied", "a person approved"),
    ]

    if error:
        projection.state = "error"
        projection.refusal = error
    elif paused_reason:
        projection.state = "paused"
        projection.refusal = paused_reason
    elif not complete:
        projection.state = "incomplete"
        projection.refusal = "the review did not finish, so this is not a verdict"
    elif any(f.severity.lower() in ("important", "blocking") for f in findings):
        projection.state = "findings"
        projection.verdict = "NEEDS-FIX"
    elif approvals_state == "observe":
        projection.state = "observe"
        projection.verdict = "APPROVE-WITH-ISSUES"
    elif approvals_state == "blocked":
        projection.state = "approvals-blocked"
        projection.verdict = "APPROVE-WITH-ISSUES"
    elif approvals_state == "satisfied":
        projection.state = "approvals-satisfied"
        # A nit or a pre-existing note is still a fact about the change. "APPROVE" claims there is
        # nothing to say, so a review with findings of any severity reports APPROVE-WITH-ISSUES.
        projection.verdict = "APPROVE-WITH-ISSUES" if findings else "APPROVE"
    elif refusal:
        projection.state = "error"
        projection.refusal = refusal
    else:
        projection.state = "findings" if findings else "approvals-satisfied"
        projection.verdict = "APPROVE-WITH-ISSUES" if findings else "APPROVE"
    projection.findings = findings
    return projection
