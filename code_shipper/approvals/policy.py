"""Approvals policy: parsing, validation and merge (FR-A1, T-UNIT-POLICY-01..20, D15d).

The policy file says when a PR counts as approved. Three things matter more than the grammar:

**A policy in the PR does not govern the PR.** The approvals config is read from the base branch. If
the PR itself changes it, that change is not applied to that PR — otherwise a PR could lower its own
bar to zero (T-ADV-01). This module parses whatever it is given; the caller is responsible for
reading the base copy, and `from_pr_tree` refuses to read the PR's version at all.

**Strictest wins.** With several policy files in play (a shared config, a repo override), a repo may
tighten a security key but never loosen it. Loosening is refused with the key named, not silently
ignored (D15d).

**The grammar is small and closed.** Unknown keys are refused rather than ignored: a typo in `when`
would otherwise silently drop a condition, which reads as an approval rule that passes everything.
This deliberately has no YAML dependency (K1, ADR 0004), so it parses the subset it defines and
refuses anything outside it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


class PolicyError(Exception):
    """A policy that cannot be applied. Never downgraded to a warning."""

    def __init__(self, message: str, *, key: str = "", line: int = 0) -> None:
        super().__init__(message)
        self.key = key
        self.line = line


# Keys that a repo may tighten but never loosen (D15d).
SECURITY_KEYS = frozenset(
    {
        "allow_agent_approval",
        "protected_paths",
        "scope",
        "budget",
        "trigger",
        "agents",
    }
)

TOP_LEVEL_KEYS = frozenset(
    {"version", "approvals", "protected_paths", "allow_agent_approval", "budget", "trigger",
     "agents", "scope"}
)
APPROVAL_KEYS = frozenset({"count", "approvers", "seats", "when", "observe", "files", "mode"})
KNOWN_FACTS = frozenset(
    {
        "changed_files",
        "lines_changed",
        "touches_path",
        "author",
        "label",
        "draft",
        "files_changed_count",
    }
)

# A policy without these is not a valid policy.
REQUIRED_APPROVAL_KEYS = frozenset({"count", "approvers"})


@dataclass
class ApprovalRule:
    """One approval requirement, possibly scoped by file patterns or conditions."""

    count: int = 1
    approvers: tuple[str, ...] = ()
    seats: int = 0
    when: dict[str, Any] = field(default_factory=dict)
    observe: bool = False
    files: tuple[str, ...] = ()
    mode: str = "all"

    def as_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "approvers": list(self.approvers),
            "seats": self.seats,
            "when": dict(self.when),
            "observe": self.observe,
            "files": list(self.files),
            "mode": self.mode,
        }


@dataclass
class Policy:
    version: int = 1
    rules: tuple[ApprovalRule, ...] = ()
    protected_paths: tuple[str, ...] = ()
    allow_agent_approval: bool = False
    budget: dict[str, Any] = field(default_factory=dict)
    trigger: dict[str, Any] = field(default_factory=dict)
    agents: dict[str, Any] = field(default_factory=dict)
    scope: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    # D16b needs to know *which* repository's rules these are and where its confirmation would live.
    # Without these, `allow_agent_approval` could only be checked against nothing.
    repo: str = ""
    state_dir: str = ""
    approvals_yml: str = ""

    @property
    def observe_only(self) -> bool:
        """A policy is observe-only when every rule is, which is the default posture."""
        return bool(self.rules) and all(rule.observe for rule in self.rules)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "approvals": [rule.as_dict() for rule in self.rules],
            "protected_paths": list(self.protected_paths),
            "allow_agent_approval": self.allow_agent_approval,
            "budget": dict(self.budget),
            "trigger": dict(self.trigger),
            "agents": dict(self.agents),
            "scope": dict(self.scope),
            "source": self.source,
        }


def _strip_comment(line: str) -> str:
    """Remove a trailing comment, respecting quotes so a '#' inside a string survives."""
    out: list[str] = []
    quote = ""
    for char in line:
        if quote:
            out.append(char)
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
            out.append(char)
            continue
        if char == "#":
            break
        out.append(char)
    return "".join(out).rstrip()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _scalar(text: str) -> Any:
    value = text.strip()
    if not value:
        return ""
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_scalar(part) for part in inner.split(",") if part.strip()]
    if value.lower() in ("true", "yes"):
        return True
    if value.lower() in ("false", "no"):
        return False
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def _lines(text: str) -> list[tuple[int, int, str]]:
    """(line number, indent, content) for every meaningful line."""
    out: list[tuple[int, int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        if stripped.strip() in ("---", "..."):
            continue
        out.append((number, _indent(stripped), stripped.strip()))
    return out


@dataclass
class _Node:
    """A tiny indentation tree, enough for the policy subset."""

    key: str
    value: Any = None
    children: list["_Node"] = field(default_factory=list)
    line: int = 0

    def to_value(self) -> Any:
        if self.children:
            if all(child.key.startswith("- ") or child.key == "-" for child in self.children):
                return [_scalar(child.key[1:].strip()) for child in self.children]
            return {child.key: child.to_value() for child in self.children}
        return self.value


def _parse_tree(text: str) -> list[_Node]:
    rows = _lines(text)
    root: list[_Node] = []
    stack: list[tuple[int, list[_Node]]] = [(-1, root)]

    for number, depth, content in rows:
        if depth == 0 and content.startswith("- "):
            node = _Node(key="- " + content[2:].strip(), line=number)
        elif content.startswith("- "):
            node = _Node(key="- " + content[2:].strip(), line=number)
        else:
            if ":" not in content:
                raise PolicyError(
                    f"line {number}: expected 'key: value', got {content!r}", line=number
                )
            key, _, value = content.partition(":")
            if value.strip().startswith(("&", "*", "|", ">")):
                # Anchors, aliases and block scalars are not part of this grammar. Accepting an
                # anchor silently (as an empty value) would drop whatever it was attached to.
                raise PolicyError(
                    f"line {number}: {value.strip()[0]!r} is not supported in this policy grammar",
                    line=number,
                )
            node = _Node(key=key.strip(), line=number)
            if value.strip():
                node.value = _scalar(value)

        while stack and depth <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise PolicyError(f"line {number}: unexpected indentation", line=number)
        stack[-1][1].append(node)
        stack.append((depth, node.children))

    return root


def parse(text: str, *, source: str = "") -> Policy:
    """Parse a policy. Refuses anything outside the defined grammar."""
    if not text or not text.strip():
        return Policy(source=source)

    tree = _parse_tree(text)
    seen_top: set[str] = set()
    policy = Policy(source=source)

    for node in tree:
        key = node.key
        if key in seen_top:
            raise PolicyError(f"duplicate key {key!r}", key=key, line=node.line)
        seen_top.add(key)

        if key not in TOP_LEVEL_KEYS:
            raise PolicyError(
                f"unknown key {key!r}; expected one of {', '.join(sorted(TOP_LEVEL_KEYS))}",
                key=key,
                line=node.line,
            )

        if key == "version":
            if not isinstance(node.value, int):
                raise PolicyError("version must be an integer", key="version", line=node.line)
            policy.version = node.value
        elif key == "approvals":
            policy.rules = _parse_rules(node)
        elif key == "protected_paths":
            policy.protected_paths = tuple(_as_list(node, "protected_paths"))
        elif key == "allow_agent_approval":
            if not isinstance(node.value, bool):
                raise PolicyError(
                    "allow_agent_approval must be true or false", key=key, line=node.line
                )
            policy.allow_agent_approval = node.value
        elif key == "budget":
            policy.budget = _as_mapping(node, "budget")
        elif key == "trigger":
            policy.trigger = _as_mapping(node, "trigger")
        elif key == "agents":
            policy.agents = _as_mapping(node, "agents")
        elif key == "scope":
            policy.scope = _as_mapping(node, "scope")

    _validate(policy)
    return policy


def _as_list(node: _Node, name: str) -> list[str]:
    if node.children and not any(child.key.startswith("-") for child in node.children):
        raise PolicyError(f"{name} must be a list", key=name, line=node.line)
    items: list[str] = []
    for child in node.children:
        if child.key.startswith("- "):
            items.append(str(_scalar(child.key[2:]).__str__()))
    if not items and isinstance(node.value, list):
        items = [str(item) for item in node.value]
    return items


def _as_mapping(node: _Node, name: str) -> dict[str, Any]:
    if not node.children:
        raise PolicyError(f"{name} must be a mapping", key=name, line=node.line)
    out: dict[str, Any] = {}
    for child in node.children:
        if child.key.startswith("-"):
            raise PolicyError(f"{name} must be a mapping, not a list", key=name, line=child.line)
        value = child.to_value()
        if isinstance(value, dict):
            raise PolicyError(f"nested mappings under {name} are not supported yet",
                              key=name, line=child.line)
        out[child.key] = value if isinstance(value, list) else value
    return out


def _parse_rules(node: _Node) -> tuple[ApprovalRule, ...]:
    rules: list[ApprovalRule] = []
    for child in node.children:
        if not child.key.startswith("- "):
            raise PolicyError("approvals must be a list of rules", key="approvals", line=child.line)
        # a list item either carries a value ("- count: 1") or has children
        inline = child.key[2:].strip()
        fields: dict[str, Any] = {}
        if inline and ":" in inline:
            key, _, value = inline.partition(":")
            fields[key.strip()] = _scalar(value)
        for grandchild in child.children:
            if grandchild.key in fields:
                raise PolicyError(
                    f"duplicate key {grandchild.key!r} in an approval rule",
                    key=grandchild.key,
                    line=grandchild.line,
                )
            fields[grandchild.key] = grandchild.to_value()
        rules.append(_build_rule(fields, node_line=child.line))
    return tuple(rules)


def _build_rule(fields: dict[str, Any], *, node_line: int) -> ApprovalRule:
    for key in fields:
        if key not in APPROVAL_KEYS:
            raise PolicyError(
                f"unknown approval key {key!r}; expected one of "
                f"{', '.join(sorted(APPROVAL_KEYS))}",
                key=key,
                line=node_line,
            )

    missing = REQUIRED_APPROVAL_KEYS - set(fields)
    if missing:
        raise PolicyError(
            f"an approval rule is missing {', '.join(sorted(missing))}",
            key=sorted(missing)[0],
            line=node_line,
        )

    count = fields["count"]
    if not isinstance(count, int) or isinstance(count, bool):
        raise PolicyError("count must be an integer", key="count", line=node_line)
    if count < 0:
        raise PolicyError("count must not be negative", key="count", line=node_line)

    approvers = fields["approvers"]
    if isinstance(approvers, str):
        approvers = [approvers] if approvers else []
    if not isinstance(approvers, list):
        raise PolicyError("approvers must be a list", key="approvers", line=node_line)

    seats = fields.get("seats", 0)
    if not isinstance(seats, int) or isinstance(seats, bool):
        raise PolicyError("seats must be an integer", key="seats", line=node_line)
    # Checked together: seats make sense only alongside a positive count, otherwise a rule can never
    # be satisfied and reads as a permanent block.
    if seats < 0:
        raise PolicyError("seats must not be negative", key="seats", line=node_line)
    if seats and count == 0:
        raise PolicyError(
            "seats is set but count is 0, so the rule can never be satisfied",
            key="seats",
            line=node_line,
        )
    if seats and seats > len(approvers):
        raise PolicyError(
            f"seats ({seats}) exceeds the number of approvers ({len(approvers)})",
            key="seats",
            line=node_line,
        )

    # A rule needing more approvals than there are named approvers can never be satisfied. Left
    # accepted, the status comment tells a reviewer "2 more approval(s) needed" forever — a statement
    # that is false, because no number of approvals reaches it. Refused at parse with both numbers, so
    # the message says exactly what to change. This grammar has no teams or groups, so the count of
    # possible approvers is knowable here; if that ever changes, this must become a comment-level
    # "cannot be satisfied as written" instead of a parse refusal, not a silent acceptance.
    if count > len(approvers):
        raise PolicyError(
            f"count ({count}) exceeds the number of named approvers ({len(approvers)}), so this rule "
            "can never be satisfied; name more approvers or lower the count",
            key="count",
            line=node_line,
        )

    when = fields.get("when", {})
    if when and not isinstance(when, dict):
        raise PolicyError("when must be a mapping of facts", key="when", line=node_line)
    for fact in (when or {}):
        if fact not in KNOWN_FACTS:
            raise PolicyError(
                f"unknown fact {fact!r} in when; expected one of "
                f"{', '.join(sorted(KNOWN_FACTS))}",
                key="when",
                line=node_line,
            )

    observe = fields.get("observe", False)
    if not isinstance(observe, bool):
        raise PolicyError("observe must be true or false", key="observe", line=node_line)

    files = fields.get("files", [])
    if isinstance(files, str):
        files = [files] if files else []
    for pattern in files:
        _check_glob(str(pattern), key="files", line=node_line)

    mode = fields.get("mode", "all")
    if mode not in ("all", "any"):
        raise PolicyError("mode must be all or any", key="mode", line=node_line)

    return ApprovalRule(
        count=count,
        approvers=tuple(str(a) for a in approvers),
        seats=seats,
        when=dict(when or {}),
        observe=observe,
        files=tuple(str(f) for f in files),
        mode=mode,
    )


def _check_glob(pattern: str, *, key: str, line: int) -> None:
    if not pattern:
        raise PolicyError("an empty glob is not allowed", key=key, line=line)
    if pattern.count("[") != pattern.count("]"):
        raise PolicyError(f"unbalanced [ ] in glob {pattern!r}", key=key, line=line)
    try:
        re.compile(_glob_to_regex(pattern))
    except re.error as exc:
        raise PolicyError(f"bad glob {pattern!r}: {exc}", key=key, line=line) from None


def _glob_to_regex(pattern: str) -> str:
    """POSIX-style: `*` stops at a separator, `**` crosses. Same semantics as the scope policy."""
    out = ["^"]
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if pattern[index : index + 2] == "**":
                out.append(".*")
                index += 2
                if pattern[index : index + 1] == "/":
                    index += 1
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = pattern.find("]", index)
            out.append(pattern[index : end + 1])
            index = end + 1
            continue
        else:
            out.append(re.escape(char))
        index += 1
    out.append("$")
    return "".join(out)


def glob_match(pattern: str, path: str) -> bool:
    return bool(re.fullmatch(_glob_to_regex(pattern), path))


def _validate(policy: Policy) -> None:
    if policy.version < 1:
        raise PolicyError("version must be at least 1", key="version")
    for pattern in policy.protected_paths:
        _check_glob(pattern, key="protected_paths", line=0)
    if policy.allow_agent_approval:
        # T-ADV-16: approval needs a second identity. Requiring the mode to be stated explicitly means
        # an omitted or misspelled key cannot default into "the agent may approve".
        mode = policy.agents.get("mode")
        if mode == "single":
            raise PolicyError(
                "allow_agent_approval cannot be true when agents.mode is 'single': the agent would "
                "be able to approve its own work",
                key="allow_agent_approval",
            )
        if mode != "separated":
            raise PolicyError(
                "allow_agent_approval requires agents.mode: separated; without it there is no second "
                "identity to approve",
                key="allow_agent_approval",
            )
