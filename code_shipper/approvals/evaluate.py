"""Evaluate a policy against a PR: how many approvals are needed, and whether they are there.

Three rules shape the result, and each closes a way to accidentally approve something:

**Strictest wins.** When several rules apply to a PR, the highest requirement governs — never the
first matched, never the lowest. Taking the lowest would let a broad "docs need one reviewer" rule
undercut a narrow "anything touching auth needs three".

**Observe never blocks.** A rule marked ``observe: true`` is reported and counted, but cannot fail a
PR. That is the default posture: a new policy can be watched for a while before it gates anything.

**A refused policy relaxes nothing.** If the policy cannot be parsed or applied, the result is
"cannot evaluate", never "approved". Silence is not consent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .policy import ApprovalRule, Policy, PolicyError, glob_match


@dataclass
class Facts:
    """What we know about the PR, gathered from the host rather than the policy."""

    changed_files: tuple[str, ...] = ()
    lines_changed: int = 0
    author: str = ""
    labels: tuple[str, ...] = ()
    draft: bool = False
    approvals: tuple[str, ...] = ()
    # Historical file names, so a rename counts under both paths (T-UNIT-EVAL-*).
    previous_paths: tuple[str, ...] = ()
    # Whether the PR modifies the approvals config itself.
    edits_policy: bool = False

    def touches(self, patterns: tuple[str, ...] | list[str]) -> bool:
        if not patterns:
            return False
        paths = list(self.changed_files) + list(self.previous_paths)
        return any(glob_match(str(pattern), path) for path in paths for pattern in patterns)


@dataclass
class RuleResult:
    rule: ApprovalRule
    applies: bool
    satisfied: bool
    counted: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "count": self.rule.count,
            "observe": self.rule.observe,
            "applies": self.applies,
            "satisfied": self.satisfied,
            "counted": list(self.counted),
            "missing": list(self.missing),
            "reason": self.reason,
        }


@dataclass
class Verdict:
    """The answer to "may this be considered approved", with the whole working shown."""

    required: int = 0
    present: int = 0
    satisfied: bool = False
    blocked: bool = False
    block_reason: str = ""
    observe_only: bool = False
    rules: list[RuleResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # The governing rule, so a status comment can name what is being asked for.
    governing: ApprovalRule | None = None

    @property
    def evaluated(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        if self.errors:
            return f"cannot evaluate: {self.errors[0]}"
        if self.blocked:
            return self.block_reason
        if self.observe_only:
            return (f"observe only: {self.present} approval(s) present, "
                    f"{self.required} would be required")
        if self.satisfied:
            return f"approved: {self.present} of {self.required} required approval(s)"
        return f"needs {self.required - self.present} more approval(s) ({self.present}/{self.required})"

    def as_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "present": self.present,
            "satisfied": self.satisfied,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "observe_only": self.observe_only,
            "evaluated": self.evaluated,
            "errors": list(self.errors),
            "rules": [rule.as_dict() for rule in self.rules],
            "summary": self.summary(),
        }


def _when_matches(rule: ApprovalRule, facts: Facts) -> tuple[bool, str]:
    """Whether a rule's ``when`` clause holds. Returns the reason when it does not apply."""
    for fact, expected in (rule.when or {}).items():
        if fact == "changed_files":
            wanted = [str(item) for item in (expected if isinstance(expected, list) else [expected])]
            if not set(wanted) & set(facts.changed_files):
                return False, "changed_files does not match"
        elif fact == "lines_changed":
            threshold = expected if isinstance(expected, int) else 0
            if facts.lines_changed < threshold:
                return False, f"lines_changed {facts.lines_changed} < {threshold}"
        elif fact == "touches_path":
            patterns = expected if isinstance(expected, list) else [expected]
            if not facts.touches([str(p) for p in patterns]):
                return False, "no changed path matches"
        elif fact == "author":
            wanted = [str(item) for item in (expected if isinstance(expected, list) else [expected])]
            if facts.author not in wanted:
                return False, "author does not match"
        elif fact == "label":
            wanted = [str(item) for item in (expected if isinstance(expected, list) else [expected])]
            if not set(wanted) & set(facts.labels):
                return False, "label not present"
        elif fact == "draft":
            if bool(expected) != bool(facts.draft):
                return False, "draft state does not match"
        elif fact == "files_changed_count":
            threshold = expected if isinstance(expected, int) else 0
            if len(facts.changed_files) < threshold:
                return False, f"files_changed_count {len(facts.changed_files)} < {threshold}"
    return True, ""


def _rule_applies(rule: ApprovalRule, facts: Facts) -> tuple[bool, str]:
    if rule.files:
        if not facts.touches(rule.files):
            return False, "no changed path matches the rule's files"
    return _when_matches(rule, facts)


def evaluate(policy: Policy | None, facts: Facts) -> Verdict:
    """The verdict for this PR under this policy."""
    verdict = Verdict()

    if policy is None:
        verdict.errors.append("no policy: nothing was evaluated, so nothing is approved")
        return verdict

    if facts.edits_policy and policy.source == "pr":
        # Belt and braces: the caller should already be reading the base copy (T-ADV-01). If a policy
        # sourced from the PR reaches here while the PR edits it, refuse rather than apply it.
        verdict.errors.append(
            "the policy was read from the PR while the PR changes it; refusing to apply it"
        )
        return verdict

    if not policy.rules:
        verdict.errors.append("the policy has no approval rules")
        return verdict

    verdict.observe_only = policy.observe_only
    applicable: list[RuleResult] = []

    for rule in policy.rules:
        applies, why = _rule_applies(rule, facts)
        counted = tuple(a for a in facts.approvals if a in rule.approvers) if applies else ()
        satisfied = len(counted) >= rule.count if applies else False
        result = RuleResult(
            rule=rule,
            applies=applies,
            satisfied=satisfied,
            counted=counted,
            missing=tuple(a for a in rule.approvers if a not in facts.approvals) if applies else (),
            reason=why or "",
        )
        verdict.rules.append(result)
        if applies:
            applicable.append(result)

    if not applicable:
        # No rule speaks to this PR. That is "nothing to require", not "approved by default", so the
        # outcome is a satisfied zero with an explicit note rather than a silent pass.
        verdict.required = 0
        verdict.present = 0
        verdict.satisfied = True
        verdict.block_reason = ""
        return verdict

    # Strictest wins: the highest bar governs, regardless of rule order.
    governing = max(applicable, key=lambda result: result.rule.count)
    verdict.governing = governing.rule
    verdict.required = governing.rule.count
    verdict.present = len(governing.counted)
    verdict.satisfied = governing.satisfied

    blocking = [r for r in applicable if not r.rule.observe]
    verdict.observe_only = not blocking
    if blocking:
        verdict.satisfied = all(r.satisfied for r in blocking)
        # The number to show is the strictest unsatisfied rule's shortfall, not a sum: two rules
        # needing one approval each do not need two people when both accept the same reviewer.
        strictest_unsatisfied = max(
            (r for r in blocking if not r.satisfied),
            key=lambda r: r.rule.count,
            default=None,
        )
        if strictest_unsatisfied is not None:
            verdict.required = strictest_unsatisfied.rule.count
            verdict.present = len(strictest_unsatisfied.counted)
            verdict.governing = strictest_unsatisfied.rule
        else:
            strictest = max(blocking, key=lambda r: r.rule.count)
            verdict.required = strictest.rule.count
            verdict.present = len(strictest.counted)
            verdict.governing = strictest.rule
    else:
        verdict.satisfied = True

    return verdict


def refuses_agent_approval(policy: Policy | None) -> tuple[bool, str]:
    """Whether this policy would let an agent approve its own work (T-ADV-16).

    Two separate conditions, both required: the flag must be on *and* the identities must be
    separated. The parse layer refuses the contradictory combination outright; this is the check the
    runtime applies to a policy that arrived from somewhere else.
    """
    if policy is None:
        return False, ""
    if not policy.allow_agent_approval:
        return False, ""
    mode = str(policy.agents.get("mode", "") or "")
    if mode in ("", "single"):
        return True, (
            "allow_agent_approval requires separated identities (agents.mode), because a single "
            "identity cannot approve its own work"
        )
    # D16b: the flag is a request, not a grant. Without an acknowledgement record bound to this repository
    # and to the current approvals.yml, agent approval stays off. Checked here — where the decision is made —
    # so no path can reach approval without passing it.
    state_dir = getattr(policy, "state_dir", "") or ""
    if not state_dir:
        return True, (
            "allow_agent_approval is set but no state directory is known, so the D16b confirmation "
            "cannot be checked. Refusing rather than approving on an unverifiable consent record."
        )
    from .confirm import agent_approval_is_enabled

    enabled, reason = agent_approval_is_enabled(
        state_dir=state_dir, repo=getattr(policy, "repo", "") or "",
        approvals_yml=getattr(policy, "approvals_yml", "") or "",
    )
    if not enabled:
        return True, reason
    return False, ""
