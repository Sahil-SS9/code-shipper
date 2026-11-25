"""Comment commands: `@code-shipper <instruction>` and `@code-shipper stop` (FR-S3, T-ADV-03).

The rule that matters here is who is allowed to steer. A comment is text, and text does not grant
authority (ADR 0002). So the permission comes from the **host API** — `permission_of(repo, login)` —
never from anything in the comment: not a claim in the body, not a label, not the author's display
name. A command from a user without write access is reported as ignored, and the reason is recorded,
because silently dropping it looks identical to a broken bot.

Parsing is deliberately literal. There is no fuzzy matching, because a comment that merely mentions
the bot should not steer it, and a parse that is generous is a parse that can be steered by accident.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# The command trigger. Anchored so "I asked code-shipper about this" is not a command; a leading @ is
# required, which is also how a human addresses the bot.
_TRIGGER = re.compile(r"^\s*@code-shipper\b(.*)$", re.IGNORECASE | re.MULTILINE)

# The stop verb, and only the stop verb. "stop the bleeding" is prose; "stop" alone is a command.
_STOP = re.compile(r"^\s*(stop|pause|cancel)\s*[.!]*\s*$", re.IGNORECASE)

WATCH_LABEL = "code-shipper-watching"


class CommandKind:
    INSTRUCTION = "instruction"
    STOP = "stop"
    IGNORED = "ignored"


@dataclass
class Command:
    kind: str
    text: str = ""
    comment_id: str = ""
    author: str = ""
    reason: str = ""
    detail: dict = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        return self.kind in (CommandKind.INSTRUCTION, CommandKind.STOP)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "text": self.text,
            "comment_id": self.comment_id,
            "author": self.author,
            "reason": self.reason,
        }


def parse(body: str) -> tuple[str, str] | None:
    """(kind, text) for a command in this body, or None if it carries no command.

    Returns the *last* command when a body has several: a human editing a comment to add "stop" at the
    end means the stop.
    """
    if not body:
        return None
    matches = list(_TRIGGER.finditer(body))
    if not matches:
        return None
    rest = matches[-1].group(1).strip()
    if not rest:
        # "@code-shipper" alone is a greeting, not an instruction. Treat it as no command rather than
        # an empty instruction that would reach the model as a blank prompt.
        return None
    if _STOP.match(rest):
        return (CommandKind.STOP, "")
    return (CommandKind.INSTRUCTION, rest)


def evaluate(comment, permission_of) -> Command:
    """Decide what a comment means, given the host's view of its author's permission.

    ``permission_of`` is called with the login and must return the host's answer: "admin",
    "maintain", "write", "read" or "none". It is injected rather than looked up so this stays testable
    without a network, and so the caller is visibly responsible for asking the host.
    """
    body = getattr(comment, "body", "") or ""
    comment_id = str(getattr(comment, "id", "") or "")
    author = getattr(comment, "author", "") or ""

    parsed = parse(body)
    if parsed is None:
        return Command(CommandKind.IGNORED, comment_id=comment_id, author=author,
                       reason="no command in this comment")

    kind, text = parsed

    permission = (permission_of(author) or "none").lower()
    if permission not in ("admin", "maintain", "write"):
        return Command(
            kind=CommandKind.IGNORED,
            text=text,
            comment_id=comment_id,
            author=author,
            reason=f"ignored: {author or 'the author'} has {permission!r} permission, "
                   "and only write access or above may steer a run",
            detail={"permission": permission},
        )

    return Command(kind=kind, text=text, comment_id=comment_id, author=author,
                   detail={"permission": permission})


def latest(commands: list[Command]) -> Command | None:
    """The command that should be acted on, or None.

    A stop outranks an instruction regardless of order: if a human has asked for a stop at any point,
    continuing because an earlier instruction was parsed first would be the wrong read.
    """
    stops = [c for c in commands if c.kind == CommandKind.STOP]
    if stops:
        return stops[-1]
    instructions = [c for c in commands if c.kind == CommandKind.INSTRUCTION]
    if instructions:
        return instructions[-1]
    return None


def should_stop(*, commands: list[Command], labelled: bool) -> tuple[bool, str]:
    """Whether the loop must stop, and why (T-SCN-04).

    Two independent ways, per FR-S3: an explicit stop command from someone with write access, or
    removing the watching label. Both are checked every round, because a loop that only checks at
    start is a loop you cannot stop.
    """
    command = latest(commands)
    if command is not None and command.kind == CommandKind.STOP:
        return True, f"stop command from {command.author or 'a writer'} (comment {command.comment_id})"
    if not labelled:
        return True, f"the {WATCH_LABEL} label was removed"
    return False, ""
