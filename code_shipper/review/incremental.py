"""Scope a diff down to the files a later push actually touched (D11, C3).

For an incremental review we need "what changed between the last reviewed head and now". GitHub gives
us a compare endpoint; but the review engine works on a `Diff`, and a compare between two commits
gives a diff whose base is the old head rather than the PR base.

Two facts make this safe to reason about without a network call of its own:

* A file that the later diff does not touch cannot contain a new problem introduced by the new
  commits, so it is out of scope for an incremental pass.
* A file that *is* touched must be reviewed in full, not just the new hunk: the model needs the
  function around the change to judge it.

So the operation is a **filter**, not a subtraction: keep whole files that appear in the
since-diff, drop the rest, and report what was dropped so the review body can say "N files unchanged
since the last review" rather than silently covering less.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..hosts.base import Diff, FilePatch


@dataclass(frozen=True)
class Scoped:
    """A diff narrowed to a range, plus what was excluded."""

    diff: Diff
    included: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()
    since: str = ""
    to: str = ""

    @property
    def empty(self) -> bool:
        return not self.diff.files

    def summary(self) -> str:
        if self.empty:
            return f"No files changed since {self.since[:7] or 'the last review'}."
        if not self.excluded:
            return f"{len(self.included)} file(s) changed since {self.since[:7]}."
        return (
            f"{len(self.included)} file(s) changed since {self.since[:7]}; "
            f"{len(self.excluded)} unchanged file(s) not re-reviewed."
        )


def _paths(diff: Diff) -> dict[str, FilePatch]:
    return {patch.path: patch for patch in diff.files}


def restrict(full: Diff, since: Diff, *, since_sha: str = "", to_sha: str = "") -> Scoped:
    """Keep only the files the ``since`` diff touches, taking content from ``full``.

    ``full`` is the diff from the PR base to the new head; ``since`` is base=last reviewed head,
    head=new head. Content comes from ``full`` because that is what the reviewer should see — a hunk
    rebased onto the last review would show a change out of context.
    """
    touched = _paths(since)
    kept: list[FilePatch] = []
    excluded: list[str] = []

    for path, patch in _paths(full).items():
        if path in touched:
            kept.append(patch)
        else:
            excluded.append(path)

    return Scoped(
        diff=Diff(files=tuple(kept)),
        included=tuple(p.path for p in kept),
        excluded=tuple(sorted(excluded)),
        since=since_sha,
        to=to_sha,
    )


def is_incremental_worthwhile(scoped: Scoped, *, minimum_files: int = 1) -> bool:
    """Whether an incremental pass has enough to look at.

    A push touching one file is worth reviewing. A push touching nothing reviewable means the earlier
    review already covered this head, so a second model call would only reproduce the first.
    """
    return len(scoped.diff.files) >= minimum_files
