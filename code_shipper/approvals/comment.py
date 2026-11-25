"""The approvals status comment (FR-A5), updated in place on every push.

One comment per PR, edited rather than appended. A new comment per push would bury the current state
under its own history, and a reviewer would have to read upwards to find out what is still missing.
The body always states the requirement, what is present, and — when it is not satisfied — exactly
what is missing, because "not approved" alone gives a reviewer nothing to act on.

Observe mode says so explicitly: "observe only" must never be readable as "approved".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .evaluate import Facts, Verdict
from .policy import Policy

# The marker used to find our own comment, so a second run edits rather than duplicates.
MARKER = "<!-- code-shipper:approvals -->"


@dataclass
class Comment:
    body: str
    should_post: bool = True
    state: str = "pending"  # pending | approved | observe | blocked | error

    def as_dict(self) -> dict[str, Any]:
        return {"body": self.body, "should_post": self.should_post, "state": self.state}


def _list(items: tuple[str, ...] | list[str]) -> str:
    return ", ".join(f"`{item}`" for item in items) if items else "none"


def render(verdict: Verdict, *, facts: Facts, policy: Policy | None) -> Comment:
    """The comment body for this verdict."""
    lines = [MARKER, "## Approvals", ""]

    if verdict.errors:
        lines += [
            f"**Cannot evaluate.** {verdict.errors[0]}",
            "",
            "Nothing is approved while the policy cannot be applied.",
        ]
        return Comment(body="\n".join(lines), state="error")

    if verdict.observe_only:
        lines += [
            f"**Observe only.** {verdict.present} approval(s) present; "
            f"{verdict.required} would be required.",
            "",
            "This policy is not blocking anything yet.",
        ]
        return Comment(body="\n".join(lines), state="observe")

    if verdict.satisfied:
        lines += [f"**Approved.** {verdict.present} of {verdict.required} required approval(s)."]
        if verdict.required == 0:
            lines += ["", "No rule requires an approval for the files in this pull request."]
        return Comment(body="\n".join(lines), state="approved")

    needed = max(0, verdict.required - verdict.present)
    lines += [
        f"**{needed} more approval(s) needed.** {verdict.present} of {verdict.required} present.",
        "",
    ]

    governing = verdict.governing
    if governing is not None:
        if governing.approvers:
            lines.append(f"Approvers accepted for this rule: {_list(governing.approvers)}")
        if governing.files:
            lines.append(f"Rule applies to: {_list(governing.files)}")
        if governing.when:
            conditions = ", ".join(f"`{key}` = `{value}`" for key, value in governing.when.items())
            lines.append(f"Conditions: {conditions}")
        if governing.seats:
            lines.append(f"Seats required: {governing.seats}")
        lines.append("")

    if facts.approvals:
        lines.append(f"Approvals so far: {_list(facts.approvals)}")
    else:
        lines.append("No approvals yet.")

    # An agent's own approval never counts, so say whose approval is being waited on.
    lines += [
        "",
        "Approval must come from a reviewer, not from this tool. Code-Shipper never approves a pull "
        "request.",
    ]
    return Comment(body="\n".join(lines), state="pending")


def should_update(previous: str | None, current: Comment) -> bool:
    """Whether to edit the comment.

    Skipped when the body is unchanged, so a webhook storm does not produce a stream of no-op edits
    that each notify watchers.
    """
    return (previous or "") != current.body


def find_existing(comments: list[Any]) -> Any | None:
    """Our own approvals comment, if the PR already has one."""
    for comment in comments:
        body = getattr(comment, "body", "") or ""
        if MARKER in body:
            return comment
    return None
