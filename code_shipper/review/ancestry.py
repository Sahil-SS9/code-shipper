"""D5: refuse to trust an incremental diff that cannot be a continuation (D5 guard).

Incremental review assumes the new head is a descendant of the last reviewed head: "only the commits
since then are new". Two situations break that assumption, and both fail *silently* if unguarded,
because the shape of the input still looks valid:

**A force-push or rebase replaces history.** The last reviewed head is no longer an ancestor of the new
head. Commits that were never reviewed are now in the PR, and a diff from the old head to the new one
describes the *replacement*, not the work that happened — so an incremental review could approve a tree
that contains code no review has ever seen.

**The head moved but the diff is empty.** A merge commit, a revert of a revert, or a base change can
move the head while changing nothing between the two heads. Reporting "nothing to review" there is a
claim about a tree that differs from the reviewed one.

Both cases fall back to a **full review**, and the review body says so. Falling back silently would be
almost as bad: a reader who believes they are seeing an incremental pass will read "N files unchanged"
as reassurance it was not.

Ancestry is decided from the compare API's `status`, which reports `ahead`/`behind`/`diverged`, rather
than by trying to fetch a commit graph we do not have.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# The reasons a guard fires. Named so the body and the tests cite the same strings.
DIVERGED = "diverged"
EMPTY_WITH_MOVED_HEAD = "empty-diff-with-moved-head"
NO_BASELINE = "no-baseline"
UNKNOWN_ANCESTRY = "unknown-ancestry"


@dataclass(frozen=True)
class GuardResult:
    """Whether an incremental pass is safe, and why not when it is not."""

    safe: bool
    reason: str = ""
    detail: str = ""

    @property
    def fell_back(self) -> bool:
        return not self.safe

    def body_note(self) -> str:
        """The sentence the review body carries when the guard forced a full review."""
        if self.safe:
            return ""
        if self.reason == DIVERGED:
            return (
                "**Full review, not incremental.** The last reviewed commit is no longer an ancestor "
                "of this head, which means history was rewritten (a force-push, a rebase, or a base "
                "change). Commits that were never reviewed are now in this pull request, so this pass "
                "covered the whole change."
            )
        if self.reason == EMPTY_WITH_MOVED_HEAD:
            return (
                "**Full review, not incremental.** The head changed but the diff between the two "
                "heads is empty, so the commits are not a straightforward continuation. This pass "
                "covered the whole change."
            )
        if self.reason == NO_BASELINE:
            return "**Full review.** There is no previously reviewed commit to compare against."
        return (
            "**Full review, not incremental.** Ancestry between the last reviewed commit and this "
            "head could not be established, so this pass covered the whole change rather than assume "
            "a continuation."
        )

    def as_dict(self) -> dict[str, Any]:
        return {"safe": self.safe, "reason": self.reason, "detail": self.detail,
                "note": self.body_note()}


def _compare_status(compare: Any) -> str:
    """The `status` field from a compare result, however the host exposes it."""
    if compare is None:
        return ""
    for attribute in ("status", "compare_status"):
        value = getattr(compare, attribute, None)
        if isinstance(value, str) and value:
            return value.lower()
    if isinstance(compare, dict):
        return str(compare.get("status", "")).lower()
    return ""


def _compare_is_empty(compare: Any) -> bool:
    """Whether the compare reported no changed files."""
    if compare is None:
        return True
    files = getattr(compare, "files", None)
    if files is None and isinstance(compare, dict):
        files = compare.get("files")
    if files is None:
        return False  # unknown, so do not claim emptiness
    try:
        return len(files) == 0
    except TypeError:
        return False


def check(
    *,
    last_reviewed_head: str,
    new_head: str,
    compare: Any,
) -> GuardResult:
    """Decide whether an incremental pass is safe.

    ``compare`` is the host's comparison between the last reviewed head and the new head. Its `status`
    is the authority on ancestry: ``ahead`` means the new head descends from the old one.
    """
    if not last_reviewed_head:
        return GuardResult(False, NO_BASELINE, "no previously reviewed head")

    if last_reviewed_head == new_head:
        # Nothing moved. Not a fallback, and not a review either — the caller's cache handles it.
        return GuardResult(True, "", "the head has not moved")

    status = _compare_status(compare)
    if not status:
        return GuardResult(
            False, UNKNOWN_ANCESTRY,
            "the host did not report a compare status, so ancestry is unknown",
        )
    if status in ("diverged", "behind"):
        return GuardResult(
            False, DIVERGED,
            f"compare status is {status!r}: the last reviewed head is not an ancestor of this head",
        )

    if _compare_is_empty(compare):
        return GuardResult(
            False, EMPTY_WITH_MOVED_HEAD,
            "the head moved but no files differ between the two commits",
        )

    return GuardResult(True, "", f"compare status is {status!r}")


def is_divergence(host: Any, repo: str, old: str, new: str) -> bool:
    """Convenience: ask the host and decide, for callers that have no compare cached.

    Returns True when history was rewritten, so the caller must do a full review.
    """
    try:
        compare = host.compare(repo, old, new)
    except Exception:  # noqa: BLE001 - an unreadable comparison is not evidence of continuity
        return True
    return not check(last_reviewed_head=old, new_head=new, compare=compare).safe
