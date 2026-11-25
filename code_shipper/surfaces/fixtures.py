"""The nine states, as fixtures (Phase 5b).

One fixture per state, shared by the tests and the screenshot run, so the picture Sahil approves and the
picture the tests pin are the same picture. Fictional SHAs and timestamps.
"""

from __future__ import annotations

from .projection import Budget, Finding, Projection, Row, build_from_review

HEAD = "9c2eca0aa5f1c8d3e2b7a49f0c1d5e6a8b3c7d92"
OLDER = "71b4cea0f3d91c8e2b6a4f7d0c5e8a1b9d3f6c20"

ROWS = [
    Row(repo="Sahil-SS9/code-shipper-sandbox", pr=25, title="Add clamp() helper", head=HEAD,
        state="findings", reason="2 important findings open at this head", needs_human=True),
    Row(repo="Sahil-SS9/code-shipper-sandbox", pr=23, title="Revert divide to use /", head=OLDER,
        state="approvals-blocked", reason="tests green; 2 approvals still needed", needs_human=True),
    Row(repo="Sahil-SS9/code-shipper-sandbox", pr=19, title="Add limits module", head=OLDER,
        state="approvals-satisfied", reason="policy satisfied; ready for a human merge",
        needs_human=False),
    Row(repo="Sahil-SS9/code-shipper-sandbox", pr=24, title="Cancellation drill", head=OLDER,
        state="stopped", reason="watch stopped; last round settled", needs_human=False),
]

FINDINGS = [
    Finding("scn05_clamp.py", 4, "important",
            "clamp() never applies the upper bound: values above high are returned unchanged"),
    Finding("scn05_clamp.py", 9, "important",
            "window() off-by-one: range(len(values) - size) drops the last full window"),
]

BUDGET = Budget(fixed_rounds=1, max_rounds=3, pushes=1, max_pushes=10, elapsed_seconds=134,
                spend_usd=None)


def _base(
    *,
    findings: list[Finding] | None = None,
    complete: bool = True,
    approvals_state: str = "blocked",
    policy_state: str = "satisfied",
    ci_state: str = "passing",
    paused_reason: str = "",
    refusal: str = "",
    error: str = "",
    budget: Budget | None = None,
) -> Projection:
    projection = build_from_review(
        repo="Sahil-SS9/code-shipper-sandbox", pr=25, title="Add clamp() helper", head=HEAD,
        reviewed_head=HEAD, findings=list(findings or []), complete=complete,
        approvals_state=approvals_state, policy_state=policy_state, ci_state=ci_state,
        paused_reason=paused_reason, refusal=refusal, error=error,
        budget=budget or BUDGET, profile="standard", runtime_owner="kensei@sandbox",
    )
    projection.rows = ROWS
    return projection


def state_empty() -> Projection:
    projection = Projection(state="empty", profile="standard", runtime_owner="kensei@sandbox")
    projection.rows = []
    return projection


def state_reviewing() -> Projection:
    projection = _base(findings=[])
    projection.state = "reviewing"
    projection.verdict = ""
    projection.log = [
        "12:38:02 lens correctness: 1 file queued",
        "12:38:19 lens security: 1 file queued",
        "12:38:40 harness: 2 of 3 lenses returned",
        "12:38:44 harness: waiting on the third lens",
    ]
    return projection


def state_findings() -> Projection:
    return _base(findings=FINDINGS)


def state_incomplete() -> Projection:
    projection = _base(findings=[FINDINGS[0]], complete=False)
    projection.log = [
        "12:38:40 harness: 2 of 3 lenses returned",
        "12:39:02 harness: the third lens returned no parseable output",
        "12:39:02 verdict withheld: an incomplete review is not a verdict",
    ]
    return projection


def state_approvals_blocked() -> Projection:
    return _base(findings=[], approvals_state="blocked")


def state_approvals_satisfied() -> Projection:
    return _base(findings=[], approvals_state="satisfied")


def state_observe() -> Projection:
    projection = _base(findings=[], approvals_state="observe")
    projection.log = [
        "policy: count 2, approvers [Sahil-SS9, reviewer-two], observe: true",
        "policy: this rule is observed, so it does not block",
    ]
    return projection


def state_paused() -> Projection:
    projection = _base(findings=[], paused_reason="per-PR review cap reached (12 of 12 reviews)",
                       approvals_state="blocked")
    projection.budget = Budget(fixed_rounds=3, max_rounds=3, pushes=4, max_pushes=10,
                               elapsed_seconds=1847, spend_usd=None)
    projection.log = [
        "12:58:11 review 12 of 12 posted",
        "12:58:12 paused: per-PR review cap reached (12 of 12 reviews)",
        "12:58:12 waiting for a human to raise the cap or stop",
    ]
    return projection


def state_stopped() -> Projection:
    projection = _base(findings=[], approvals_state="blocked")
    projection.state = "stopped"
    projection.verdict = ""
    projection.log = [
        "13:01:40 stop signal read from the PR",
        "13:01:41 round 4 settled, nothing half-pushed",
        "13:01:41 watch stopped",
    ]
    return projection


def state_error() -> Projection:
    projection = _base(
        findings=[],
        error="count (2) exceeds the number of named approvers (1), so this rule can never be "
              "satisfied; name more approvers or lower the count",
    )
    projection.log = [
        "policy: parse refused",
        "policy: never applied; the PR was not evaluated against a rule that cannot be met",
    ]
    return projection


ALL = {
    "empty": state_empty,
    "reviewing": state_reviewing,
    "findings": state_findings,
    "incomplete": state_incomplete,
    "approvals-blocked": state_approvals_blocked,
    "approvals-satisfied": state_approvals_satisfied,
    "observe": state_observe,
    "paused": state_paused,
    "stopped": state_stopped,
    "error": state_error,
}

# The nine states Sahil asked to see, in the order he listed them, plus observe.
SCREENSHOT_ORDER = (
    "empty", "reviewing", "findings", "incomplete", "approvals-blocked",
    "approvals-satisfied", "observe", "paused", "stopped", "error",
)
