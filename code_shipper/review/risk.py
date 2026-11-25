"""Merge risk: a 5-level advisory rating with reasons (D15a, Phase 4b).

Two properties matter more than the rating itself:

**It is advisory and cannot gate anything.** The approvals evaluator must not be able to read it — the
guardrail is a structural test, not a convention, because "the score is only advisory" is worth nothing
if the policy engine can consult it. A risk score that can block a merge is a second, undocumented
approval system.

**Every level comes with its reasons.** A number with no working is unfalsifiable: a reader cannot
disagree with "3/5". So the result always carries the specific facts that produced it, and the rating is
the *maximum* of its component risks rather than an average — averaging lets one severe signal be
diluted by several mild ones, which is exactly the wrong behaviour for a risk rating.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The scale, lowest to highest. Names rather than numbers, because "moderate" is checkable and "3" is
# not.
LEVELS = ("low", "guarded", "moderate", "elevated", "high")

# Signals that carry a fixed floor, whatever else the PR touches. These are the things where being
# wrong is expensive regardless of how small the diff is.
_SEVERE_PATHS = (
    (("**/migrations/**", "**/migration/**", "**/migrate/*.py"), 3,
     "touches a database migration"),
    (("**/auth/**", "**/authn/**", "**/authz/**", "**/security/**"), 3,
     "touches authentication or security code"),
    ((".github/workflows/**", ".github/actions/**"), 3,
     "touches CI configuration, which can change what is verified"),
    (("**/Dockerfile", "**/docker-compose*.yml", "**/*.tf", "**/k8s/**"), 2,
     "touches deployment or infrastructure"),
    (("**/package-lock.json", "**/poetry.lock", "**/Cargo.lock", "**/go.sum", "**/requirements*.txt"),
     2, "changes dependency pins"),
)


@dataclass
class RiskFactor:
    """One reason the rating is what it is."""

    level: int
    reason: str
    evidence: str = ""

    @property
    def name(self) -> str:
        return LEVELS[min(max(self.level, 0), len(LEVELS) - 1)]

    def as_dict(self) -> dict[str, Any]:
        return {"level": self.level, "name": self.name, "reason": self.reason,
                "evidence": self.evidence}


@dataclass
class RiskScore:
    level: int = 0
    factors: list[RiskFactor] = field(default_factory=list)

    @property
    def name(self) -> str:
        return LEVELS[min(max(self.level, 0), len(LEVELS) - 1)]

    def summary(self) -> str:
        if not self.factors:
            return f"Merge risk: {self.name} — no elevated signals in this change."
        reasons = "; ".join(factor.reason for factor in self.factors[:3])
        more = "" if len(self.factors) <= 3 else f" (+{len(self.factors) - 3} more)"
        return f"Merge risk: {self.name} — {reasons}{more}."

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "name": self.name,
            "summary": self.summary(),
            "factors": [factor.as_dict() for factor in self.factors],
        }


def _glob_match(pattern: str, path: str) -> bool:
    from ..approvals.policy import glob_match

    return glob_match(pattern, path)


def score(
    *,
    changed_files: tuple[str, ...] | list[str],
    lines_changed: int = 0,
    findings: list[Any] | None = None,
    reverts: bool = False,
    touches_tests: bool | None = None,
) -> RiskScore:
    """Rate the merge risk of this change, with the reasons attached.

    The rating is the highest single factor, not an average. One severe signal is not made safer by
    twenty mild ones, and an average would let a large but boring diff mask a migration change.
    """
    factors: list[RiskFactor] = []
    paths = [str(path) for path in changed_files]

    for patterns, level, reason in _SEVERE_PATHS:
        hits = [path for path in paths
                if any(_glob_match(pattern, path) for pattern in patterns)]
        if hits:
            factors.append(RiskFactor(level, reason, ", ".join(hits[:3])))

    # Size: a large diff is harder to review, and the thresholds are deliberately generous so that a
    # normal feature does not read as risky.
    if lines_changed > 800:
        factors.append(RiskFactor(4, f"very large change ({lines_changed} lines)",
                                  f"{lines_changed} lines changed"))
    elif lines_changed > 300:
        factors.append(RiskFactor(2, f"large change ({lines_changed} lines)",
                                  f"{lines_changed} lines changed"))

    # Important findings are a risk signal in their own right: the review already decided they matter.
    important = [f for f in (findings or [])
                 if str(getattr(f, "severity", "")).lower() in ("important", "blocking")]
    if important:
        factors.append(RiskFactor(
            3, f"{len(important)} important finding(s) open", important[0].title if important else ""
        ))

    if reverts:
        factors.append(RiskFactor(
            3, "this change reverts earlier work", "a revert commit is present"
        ))

    # Tests: absence is a mild signal, presence settles it. Only stated when known, so an unknown
    # never masquerades as a finding.
    if touches_tests is False and lines_changed > 50:
        factors.append(RiskFactor(
            1, "no test changes alongside a non-trivial change", "no test file touched"
        ))

    level = max((factor.level for factor in factors), default=0)
    factors.sort(key=lambda factor: (-factor.level, factor.reason))
    return RiskScore(level=level, factors=factors)


def risks_a_migration(score_value: RiskScore) -> bool:
    """Whether a migration is among the reasons. Used by callers that want to escalate, never by the
    approvals evaluator."""
    return any("migration" in factor.reason for factor in score_value.factors)
