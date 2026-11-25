"""Incremental review state (D11, C3).

The question this answers: when a PR gets a second push, what should the review cover?

Only the commits since the last **complete** review. That distinction is the whole design:

* `last_reviewed_head` moves only when a review finished completely. An incomplete review — a lens
  that failed, a budget that ran out — leaves it where it was, so the next pass re-covers the same
  commits rather than skipping them. A marker advanced by "we tried" would silently drop the range a
  failed run never actually looked at.
* It is never moved by ledger activity, a comment, a cache hit, or anything that is not a review.

The union rule is the other half, and it exists because incremental review has a specific way of
being dangerous: an important finding from commit 1 that nobody fixed must not disappear because
commit 3 was the only thing reviewed. So `important_open` is the union of open important findings
over the **whole PR**, not the last pass. An unfixed important finding keeps blocking forever.

State lives next to the review cache, keyed on (repo, pr), and is written only on a complete review.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .fingerprint import fingerprint as _compute_fingerprint


@dataclass
class ReviewState:
    """What we know about a PR between reviews."""

    repo: str = ""
    pr: int = 0
    last_reviewed_head: str = ""
    # fingerprint -> the finding, for every important finding not yet seen to be resolved
    open_important: dict[str, dict[str, Any]] = field(default_factory=dict)
    # fingerprint -> the finding, for everything still open (used for dedupe across pushes)
    open_all: dict[str, dict[str, Any]] = field(default_factory=dict)
    reviews: int = 0
    last_review_at: float = 0.0
    paused: bool = False
    pause_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "pr": self.pr,
            "last_reviewed_head": self.last_reviewed_head,
            "open_important": self.open_important,
            "open_all": self.open_all,
            "reviews": self.reviews,
            "last_review_at": self.last_review_at,
            "paused": self.paused,
            "pause_reason": self.pause_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReviewState":
        return cls(
            repo=str(data.get("repo", "")),
            pr=int(data.get("pr", 0) or 0),
            last_reviewed_head=str(data.get("last_reviewed_head", "")),
            open_important=dict(data.get("open_important") or {}),
            open_all=dict(data.get("open_all") or {}),
            reviews=int(data.get("reviews", 0) or 0),
            last_review_at=float(data.get("last_review_at", 0) or 0),
            paused=bool(data.get("paused", False)),
            pause_reason=str(data.get("pause_reason", "")),
        )

    # -- the questions callers ask ----------------------------------------

    @property
    def has_baseline(self) -> bool:
        """Whether a complete review has ever run, and therefore whether "since" means anything."""
        return bool(self.last_reviewed_head)

    def is_incremental(self, head_sha: str) -> bool:
        """Whether reviewing ``head_sha`` should cover only the commits after the baseline."""
        return self.has_baseline and self.last_reviewed_head != head_sha

    def important_fingerprints(self) -> set[str]:
        """The union rule: every important finding still open, across all passes."""
        return set(self.open_important)


class ReviewStore:
    """Persistence for review state. One file per (repo, pr), under the state dir."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root) / "review-state"

    def _path(self, repo: str, pr: int) -> Path:
        safe = repo.replace("/", "__")
        return self.root / f"{safe}-{int(pr)}.json"

    def load(self, repo: str, pr: int) -> ReviewState:
        """The stored state, or a fresh one.

        A corrupt file is a fresh state rather than a crash: losing the baseline means the next review
        is a full one, which is safe. Reusing a corrupt baseline could skip commits.
        """
        path = self._path(repo, pr)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ReviewState(repo=repo, pr=pr)
        if not isinstance(data, dict):
            return ReviewState(repo=repo, pr=pr)
        state = ReviewState.from_dict(data)
        state.repo, state.pr = repo, int(pr)
        return state

    def save(self, state: ReviewState) -> None:
        path = self._path(state.repo, state.pr)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(state.as_dict(), indent=2, sort_keys=True), encoding="utf-8")
        except OSError:
            # Losing the write means the next review is a full one; never a reason to fail a review.
            pass


def advance(
    state: ReviewState,
    *,
    head_sha: str,
    complete: bool,
    findings: list[Any],
    covered_paths: list[str] | None = None,
    now: float | None = None,
) -> ReviewState:
    """Update state after a review.

    ``complete`` is the only thing that may move ``last_reviewed_head``. When it is False the baseline
    stays put and the open-findings map is left alone: an incomplete pass has not looked at the code,
    so it has no standing to declare anything resolved.

    ``covered_paths`` is what the pass actually read. It is required for clearing anything: a review
    that looked at one file cannot say a finding in another is fixed, and treating "not reported" as
    "resolved" would make the union rule meaningless.
    """
    if not complete:
        return state

    seen = set()
    for finding in findings:
        fingerprint = getattr(finding, "fingerprint", "") or ""
        if not fingerprint:
            # Stamp it here rather than requiring every caller to: a finding that reaches state
            # without an identity is invisible to the union rule, which is the failure mode the rule
            # exists to prevent. Measured on sandbox PR #19, where an important finding was reported
            # but open_important stayed empty because nothing had computed a fingerprint yet.
            fingerprint = _compute_fingerprint(
                path=getattr(finding, "path", ""),
                title=getattr(finding, "title", ""),
                evidence=getattr(finding, "evidence", ""),
            )
            if fingerprint:
                try:
                    object.__setattr__(finding, "fingerprint", fingerprint)
                except (AttributeError, TypeError):
                    pass
        if not fingerprint:
            continue
        seen.add(fingerprint)
        payload = finding.as_dict() if hasattr(finding, "as_dict") else {"title": str(finding)}
        state.open_all[fingerprint] = payload
        if (getattr(finding, "severity", "") or "").lower() in ("important", "blocking"):
            state.open_important[fingerprint] = payload

    # Only a finding in a file this pass actually covered can be cleared by not being reported.
    covered = set(covered_paths) if covered_paths is not None else set()
    for fingerprint, payload in list(state.open_all.items()):
        if fingerprint in seen:
            continue
        path = (payload or {}).get("path", "")
        if covered and path and path not in covered:
            continue
        if not covered and covered_paths is not None:
            # The pass reviewed nothing, so it can clear nothing.
            continue
        state.open_all.pop(fingerprint, None)
        # Important findings stay open until a pass that covered them stops reporting them.
        if (payload or {}).get("severity", "").lower() in ("important", "blocking"):
            state.open_important.pop(fingerprint, None)

    state.last_reviewed_head = head_sha
    state.reviews += 1
    state.last_review_at = now if now is not None else time.time()
    # A successful review clears a pause: the thing that paused it (usually a cap) has moved on.
    state.paused = False
    state.pause_reason = ""
    return state


def pause(state: ReviewState, reason: str) -> ReviewState:
    """Stop reviewing this PR, and say why.

    A pause is stated, never silent: a bot that stops reviewing without saying so is indistinguishable
    from one that broke (C12).
    """
    state.paused = True
    state.pause_reason = reason
    return state
