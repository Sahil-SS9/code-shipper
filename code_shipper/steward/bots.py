"""N6: comments from other bots are claims to check, never instructions.

Teams often run more than one automated reviewer. Their comments are useful leads and dangerous orders:
a bot's text is written by whoever controls the bot, or by whatever the PR made it say. So:

- A comment from an account ending in `[bot]` is ignored unless the user names that bot with
  `--trust-bot NAME`. Ignored means it never becomes work and never reaches a prompt; it is still counted
  in the receipt so silence is not mistaken for absence.
- A named bot's comment becomes a *claim*. In a review it reaches the lenses inside untrusted framing as
  something to check against the diff, and the same verifier as every other finding decides what survives.
  In a watch it becomes a work item whose instruction is to confirm the claim in the code first and change
  nothing if the code does not confirm it.
- It is never treated as a command: `@code-shipper` commands already require write permission read from
  the host, and a bot account's text is not a person's request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_CLAIM_CHARS = 2_000
MAX_CLAIMS_CHARS = 8_000


def is_bot(login: str) -> bool:
    return str(login or "").lower().endswith("[bot]")


def _bare(login: str) -> str:
    name = str(login or "").strip().lower()
    return name[: -len("[bot]")] if name.endswith("[bot]") else name


@dataclass
class SplitComments:
    people: list[Any] = field(default_factory=list)
    claims: list[Any] = field(default_factory=list)
    ignored_bots: list[str] = field(default_factory=list)


def split_comments(comments: list[Any], trusted: tuple[str, ...] | list[str] = ()) -> SplitComments:
    """People, named bots (claims), and every other bot (ignored, by login)."""
    named = {_bare(name) for name in trusted if str(name).strip()}
    out = SplitComments()
    for comment in comments:
        author = str(getattr(comment, "author", "") or "")
        if not is_bot(author):
            out.people.append(comment)
        elif _bare(author) in named:
            out.claims.append(comment)
        else:
            out.ignored_bots.append(author)
    return out


def claims_text(comments: list[Any], *, limit: int = MAX_CLAIMS_CHARS) -> str:
    """The named bots' comments as one block of claims, newest last, capped."""
    parts: list[str] = []
    budget = limit
    for comment in comments:
        body = str(getattr(comment, "body", "") or "").strip()
        if not body or budget <= 0:
            continue
        where = ""
        if getattr(comment, "path", None):
            where = f" on {comment.path}" + (f":{comment.line}" if getattr(comment, "line", None) else "")
        entry = f"{getattr(comment, 'author', 'bot')}{where}: {body[:MAX_CLAIM_CHARS]}"
        entry = entry[:budget]
        budget -= len(entry)
        parts.append(entry)
    return "\n\n".join(parts)
