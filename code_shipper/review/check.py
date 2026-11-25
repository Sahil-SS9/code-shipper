"""Review state as a GitHub check run (D13).

The correction that shapes this file: **on GitHub, a `neutral` conclusion satisfies a required check.**
So `neutral` is a pass in every way that matters to branch protection. An incomplete review and an
observe-only review must therefore never produce it, and the mapping is written so that no state outside
a genuinely complete review can produce `success` or `neutral`.

The honest mapping:

    complete review, findings        → failure         (something to fix)
    complete review, no findings     → success
    incomplete review                → action_required  (someone must look; not a pass, not a fail)
    observe only                     → NO CHECK AT ALL  (writing a passing check for a rule that is
                                                         not enforced is worse than writing nothing)
    error                            → failure

Only `success` and `failure` are ever produced by a *review*. `action_required` is what an incomplete run
produces, and it blocks a required check exactly as a person would expect — as a thing to look at rather
than a verdict.

Nothing here can approve. There is no conclusion shaped like an approval, and no state maps to one; the
check name is a module constant so a caller cannot impersonate another gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# The one check name. A caller-supplied name would let a caller write a check called `ci/tests` and
# impersonate another gate, so it is a constant and `write` refuses anything else.
CHECK_NAME = "code-shipper/review"

# The complete conclusion vocabulary. `neutral` is ABSENT deliberately: on GitHub it satisfies a required
# check, so for a review tool it is a pass wearing a different word.
ALLOWED_CONCLUSIONS = frozenset({"success", "failure", "action_required"})

# States that produce no check at all.
NO_CHECK_STATES = frozenset({"observe", "empty"})

# The mapping, per projection state. Keys are the projection's states, so a new state must be added here
# explicitly rather than falling through to a default that happens to be green.
STATE_CONCLUSIONS: dict[str, str | None] = {
    "reviewing": "action_required",      # in flight: not a pass while it runs
    "findings": "failure",
    "incomplete": "action_required",
    "approvals-blocked": "success",      # the review is complete; approvals are a human gate
    "approvals-satisfied": "success",
    "observe": None,
    "paused": "action_required",
    "stopped": "action_required",
    "error": "failure",
    "empty": None,
}

# Titles must not claim approval. Asserted in tests.
FORBIDDEN_WORDS = ("approved", "approve", "merge", "lgtm")


class CheckRefused(Exception):
    """A check write that will not happen. ``code`` is stable."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def conclusion_for(state: str, *, complete: bool) -> str | None:
    """The conclusion for a projection state, or None for "write no check".

    `complete` is passed separately rather than inferred, because the projection's state is a rendered
    summary and this decision must not depend on the summary being right. An incomplete review wins over
    whatever the state says.
    """
    if not complete:
        return "action_required"
    if state in NO_CHECK_STATES:
        return None
    conclusion = STATE_CONCLUSIONS.get(state)
    if conclusion is None and state in NO_CHECK_STATES:
        return None
    if conclusion is None and state not in STATE_CONCLUSIONS:
        # An unknown state is not a pass. Refuse rather than guess.
        raise CheckRefused("unknown_state", f"{state!r} has no mapping; refusing to guess a conclusion")
    return conclusion


def assert_not_a_pass(conclusion: str | None, *, complete: bool, state: str) -> None:
    """Structural guard: a non-complete or observe review must not be publishable as a pass."""
    if conclusion is None:
        return
    if conclusion not in ALLOWED_CONCLUSIONS:
        raise CheckRefused("bad_conclusion", f"{conclusion!r} is not allowed")
    if not complete and conclusion in ("success", "neutral"):
        raise CheckRefused(
            "incomplete_is_not_a_pass",
            "an incomplete review must not produce a passing conclusion",
        )
    if state in NO_CHECK_STATES and conclusion is not None:
        raise CheckRefused("observe_writes_no_check", f"{state} must write no check")


@dataclass
class CheckRequest:
    repo: str
    head_sha: str
    state: str
    complete: bool
    title: str = ""
    summary: str = ""
    details_url: str = ""

    def conclusion(self) -> str | None:
        return conclusion_for(self.state, complete=self.complete)

    def as_payload(self, *, external_id: str = "") -> dict[str, Any] | None:
        """The GitHub payload, or None when no check should be written."""
        conclusion = self.conclusion()
        if conclusion is None:
            return None
        assert_not_a_pass(conclusion, complete=self.complete, state=self.state)
        title = self.title or f"Review: {self.state}"
        lowered = title.lower()
        for word in FORBIDDEN_WORDS:
            if word in lowered:
                raise CheckRefused(
                    "approval_language", f"a check title may not say {word!r}: {title!r}"
                )
        payload: dict[str, Any] = {
            "name": CHECK_NAME,
            "head_sha": self.head_sha,
            "status": "completed",
            "conclusion": conclusion,
            "output": {"title": title, "summary": self.summary or ""},
        }
        if self.details_url:
            payload["details_url"] = self.details_url
        if external_id:
            payload["external_id"] = external_id
        return payload


def assert_sha_is_head(*, requested: str, observed_head: str) -> None:
    """A check on a SHA that is not the head describes a tree nobody is merging."""
    if not requested:
        raise CheckRefused("no_sha", "a head SHA is required")
    if not observed_head:
        raise CheckRefused("no_observed_head", "the pull request's head could not be read")
    if requested != observed_head:
        raise CheckRefused(
            "not_the_head",
            f"{requested[:8]} is not the pull request's head ({observed_head[:8]})",
        )
