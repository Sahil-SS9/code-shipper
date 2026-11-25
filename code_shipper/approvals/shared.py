"""Shared settings with tighten-only enforcement, and the effective-config preview (D15d, C13).

A central config lets repos inherit security settings. The one rule that makes this safe: **a repo may
tighten a security key but never loosen it.** Without that, "inherit the central policy" would be a
way for any repo to opt out of it by writing a permissive local file — inheritance would become a
route around the rules rather than a way to apply them.

Loosening is *refused with the key named*, not silently dropped. A silently ignored override looks
exactly like one that was applied, which is how a repo ends up believing it has a stricter setting
than it does.

`effective()` reports every value plus where it came from, because "the config says X" is not useful
when three files could have said it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .policy import Policy, PolicyError, glob_match

# Which direction each security key tightens in. Keys not listed are not security keys, so a repo may
# set them freely.
_TIGHTENING_DIRECTION = {
    "allow_agent_approval": "false",   # tightening means making it false
    "protected_paths": "more",         # tightening means more protected paths
    "scope": "narrower",               # tightening means a smaller allowlist
    "budget": "lower",                 # tightening means smaller ceilings
    "trigger": "off",                  # tightening means disabling
    "agents": "separated",             # tightening means not collapsing identities
}


@dataclass
class Merged:
    """The effective policy, with provenance for each key."""

    policy: Policy = field(default_factory=lambda: Policy(source="merged"))
    provenance: dict[str, str] = field(default_factory=dict)
    refused: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        payload = self.policy.as_dict()
        payload["provenance"] = dict(self.provenance)
        payload["refused"] = list(self.refused)
        return payload


def _protected_tightens(central: tuple[str, ...], local: tuple[str, ...]) -> bool:
    """A local list tightens only if it covers everything the central one does."""
    return set(central).issubset(set(local))


def _budget_tightens(central: dict, local: dict) -> bool:
    """Lower ceilings tighten. A key the local file omits inherits, which is neither tighter nor
    looser."""
    for key, value in local.items():
        if key in central:
            try:
                if int(value) > int(central[key]):
                    return False
            except (TypeError, ValueError):
                raise PolicyError(
                    f"budget.{key} must be a number to be merged", key=f"budget.{key}"
                ) from None
    return True


def _covered_by(local_pattern: str, central_patterns: set[str]) -> bool:
    """Whether a local allowlist pattern is already permitted by the central list.

    A string subset test is wrong here: `src/a.py` is narrower than `src/**` but not a subset of it,
    so a genuine tightening was refused as a loosening. Instead, treat the local pattern as a path and
    ask whether the central globs match it — plus the equality case, for patterns containing wildcards
    that match each other exactly.

    Limitation, stated rather than hidden: this compares patterns as strings, so a local pattern that is
    *semantically* narrower than a central one but textually unlike it (for example `src/[ab].py` under
    a central `src/**`) is accepted only because `src/[ab].py` happens to match `src/**` as a path. A
    pattern that a central glob would not match textually is treated as a loosening, which fails safe.
    """
    return local_pattern in central_patterns or any(
        glob_match(central, local_pattern) for central in central_patterns
    )


def _scope_tightens(central: dict, local: dict) -> bool:
    """A narrower allowlist tightens; a broader one loosens.

    For `allowed`: every local pattern must already be permitted centrally. For `forbidden`: everything
    central forbids must stay forbidden, so a repo may add to the list but never drop from it.
    """
    central_allowed = set(central.get("allowed") or [])
    local_allowed = [str(item) for item in (local.get("allowed") or [])]
    if central_allowed and local_allowed:
        for pattern in local_allowed:
            if not _covered_by(pattern, central_allowed):
                return False  # something the repo now permits was not permitted centrally

    central_forbidden = set(central.get("forbidden") or [])
    local_forbidden = set(local.get("forbidden") or [])
    if central_forbidden and not all(
        _covered_by(str(pattern), local_forbidden) for pattern in central_forbidden
    ):
        return False  # a central forbidden path was dropped
    return True


def merge(central: Policy | None, local: Policy | None, *, repo: str = "") -> Merged:
    """Merge a central policy with a repo's own, refusing any loosening of a security key."""
    merged = Merged()

    if central is None and local is None:
        merged.policy = Policy(source="none")
        return merged

    if central is None:
        merged.policy = local or Policy(source="none")  # type: ignore[assignment]
        merged.policy.source = "repo"
        for key in _key_names(local):
            merged.provenance[key] = "repo"
        return merged

    if local is None:
        merged.policy = central
        merged.policy.source = "central"
        for key in _key_names(central):
            merged.provenance[key] = "central"
        return merged

    result = Policy(
        version=max(central.version, local.version),
        # The central rules ALWAYS apply. A repo adds requirements; it never replaces them. This was
        # `local.rules or central.rules`, which let a repo replace a count-2 rule with a count-1 rule,
        # name its own approvers, or mark a rule observe and thereby retire it — with `refused` empty,
        # so the merge claimed to have refused nothing. Approval rules are the most important security
        # key in the file, so wholesale replacement was the worst possible place to have a hole.
        rules=tuple(central.rules) + tuple(local.rules),
        protected_paths=central.protected_paths,
        allow_agent_approval=central.allow_agent_approval,
        budget=dict(central.budget),
        trigger=dict(central.trigger),
        agents=dict(central.agents),
        scope=dict(central.scope),
        source="merged",
    )
    merged.provenance["approvals"] = (
        "central + repo (added)" if local.rules else "central"
    )

    # Name every attempt to weaken a central rule. The central rule still applies, so these are
    # ineffective — but a silently ineffective override is indistinguishable from an applied one, and
    # a repo would believe it had relaxed a rule it had not.
    for local_rule in local.rules:
        for central_rule in central.rules:
            if not _rules_overlap(central_rule, local_rule):
                continue
            if local_rule.observe and not central_rule.observe:
                merged.refused.append(
                    "approvals: the repo marked a rule observe where the central config enforces it; "
                    "the central rule still applies"
                )
            if local_rule.count < central_rule.count:
                merged.refused.append(
                    f"approvals: the repo asked for {local_rule.count} approval(s) where the central "
                    f"config requires {central_rule.count}; the central rule still applies"
                )
            if central_rule.approvers and not set(local_rule.approvers) & set(
                central_rule.approvers
            ):
                merged.refused.append(
                    "approvals: the repo named approvers "
                    f"({', '.join(local_rule.approvers) or 'none'}) who cannot satisfy the central "
                    f"rule, which requires {', '.join(central_rule.approvers)}; those approvals are "
                    "still required"
                )

    # allow_agent_approval: the central value wins unless the repo turns it off, which is tightening.
    if local.allow_agent_approval != central.allow_agent_approval:
        if local.allow_agent_approval:
            merged.refused.append(
                "allow_agent_approval: the repo set it true where the central config sets it false, "
                "which loosens a security key; keeping false"
            )
        else:
            result.allow_agent_approval = False
            merged.provenance["allow_agent_approval"] = "repo (tightened)"
    else:
        merged.provenance["allow_agent_approval"] = "central"

    # protected_paths: the union tightens, and anything the repo drops is refused.
    if local.protected_paths:
        dropped = set(central.protected_paths) - set(local.protected_paths)
        if dropped:
            merged.refused.append(
                "protected_paths: the repo dropped "
                f"{', '.join(sorted(dropped))} from the protected list, which loosens a security "
                "key; keeping them"
            )
        result.protected_paths = tuple(dict.fromkeys(
            tuple(central.protected_paths) + tuple(local.protected_paths)
        ))
        merged.provenance["protected_paths"] = "central + repo (widened)"
    else:
        merged.provenance["protected_paths"] = "central"

    for key, name in (("budget", "budget"), ("scope", "scope"), ("trigger", "trigger"),
                      ("agents", "agents")):
        central_value = getattr(central, key) or {}
        local_value = getattr(local, key) or {}
        if not local_value:
            merged.provenance[name] = "central"
            continue
        if not central_value:
            setattr(result, key, dict(local_value))
            merged.provenance[name] = "repo"
            continue
        tightens = True
        if key == "budget":
            tightens = _budget_tightens(central_value, local_value)
        elif key == "scope":
            tightens = _scope_tightens(central_value, local_value)
        elif key == "agents":
            # Collapsing to a single identity where the central config separates them is a loosening.
            if central_value.get("mode") and local_value.get("mode") == "single":
                tightens = False
        elif key == "trigger":
            # Turning a trigger back on where central disabled it is a loosening.
            if central_value.get("enabled") is False and local_value.get("enabled") is True:
                tightens = False

        if not tightens:
            merged.refused.append(
                f"{key}: the repo's value loosens the central setting, which is not allowed; "
                "keeping the central value"
            )
            merged.provenance[name] = "central (refused)"
        else:
            combined = dict(central_value)
            combined.update(local_value)
            setattr(result, key, combined)
            merged.provenance[name] = "central + repo (tightened)"

    if repo:
        merged.provenance["repo"] = repo
    merged.policy = result
    return merged


def _rules_overlap(central_rule: Any, local_rule: Any) -> bool:
    """Whether two rules could be talking about the same pull requests.

    A local rule that scopes itself to `docs/**` is not trying to weaken a central rule for `auth/**`,
    so it should not be reported as a loosening attempt. Two unscoped rules always overlap, which is
    the case that matters: an unscoped local rule is a direct competitor to every central rule.
    """
    if not central_rule.files or not local_rule.files:
        return True
    return bool(set(central_rule.files) & set(local_rule.files))


def _key_names(policy: Policy | None) -> list[str]:
    if policy is None:
        return []
    names = ["approvals"]
    for key in ("protected_paths", "allow_agent_approval", "budget", "trigger", "agents", "scope"):
        if getattr(policy, key):
            names.append(key)
    return names


def effective(merged: Merged) -> str:
    """A printable table of the merged config and where each value came from (C13)."""
    lines = ["key                     value                     source",
             "----------------------  ------------------------  ----------------------"]
    policy = merged.policy
    lines.append(f"{'allow_agent_approval':22}  {str(policy.allow_agent_approval):24}  "
                 f"{merged.provenance.get('allow_agent_approval', '-')}")
    lines.append(f"{'protected_paths':22}  {str(len(policy.protected_paths)) + ' pattern(s)':24}  "
                 f"{merged.provenance.get('protected_paths', '-')}")
    lines.append(f"{'approvals':22}  {str(len(policy.rules)) + ' rule(s)':24}  "
                 f"{merged.provenance.get('approvals', '-')}")
    for key in ("budget", "trigger", "agents", "scope"):
        value = getattr(policy, key) or {}
        lines.append(f"{key:22}  {str(len(value)) + ' setting(s)':24}  "
                     f"{merged.provenance.get(key, '-')}")
    if merged.refused:
        lines += ["", "refused (a repo may tighten but never loosen):"]
        lines += [f"  - {reason}" for reason in merged.refused]
    return "\n".join(lines)
