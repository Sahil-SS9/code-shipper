"""Load a stored review and turn it into the one projection every surface draws.

This module exists because of a specific failure: every surface was drawing `fixtures`, so the screens
looked right while showing nobody's actual review. A `--demo` flag preserves that for design reviews, but
the default path must read real state or the surfaces are decoration.

One function, `load_projection`, used by `status`, `ui`, `ship-page`, `ship-ui` and the plugin. It is the
only place that knows how stored state, the tracked ledger and the review outcomes are assembled — a
second copy would be a second set of rules about what a user is looking at.

What is stored, and therefore what a projection can honestly show:

* `ReviewStore` — last reviewed head, the open important and open findings with their fields, whether the
  review paused and why.
* `Ledger` — findings a write-access user asked to track, with actor and timestamp.
* `ReviewCache` — the last review payload at a head, which is the richest source of findings detail.

What is **not** stored: the approvals evaluation as of that run, the CI state, and the budget. Those are
facts about a moment, not about the pull request. Rather than invent them, the loader leaves them at their
"unknown" value and the projection says so — a surface that guessed would be doing the thing this whole
module exists to stop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..review.cache import ReviewCache
from ..review.state import ReviewStore
from ..review.tracked import Ledger
from .projection import Finding, Projection, Row, build_from_review

# Where state lives, matching `cli._state_dir`. One definition, imported by the CLI.
def default_state_dir() -> Path:
    import os

    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "code-shipper"


@dataclass
class StoreSnapshot:
    """Everything the surfaces can know about one pull request, read from disk.

    `found` is the important field: a projection built from a repository the tool has never reviewed must
    say so rather than render an empty, clean-looking board.
    """

    repo: str
    pr: int
    found: bool = False
    head: str = ""
    reviewed_head: str = ""
    findings: list[Finding] = field(default_factory=list)
    complete: bool = False
    paused: bool = False
    pause_reason: str = ""
    reviews: int = 0
    last_review_at: float = 0.0
    tracked: list[dict[str, Any]] = field(default_factory=list)
    title: str = ""
    budget_rounds: int = 0


def _finding_from_dict(row: dict[str, Any]) -> Finding:
    """A stored finding dict into the projection's shape.

    Tolerant on purpose: state written by an older version must still render. Anything missing becomes
    empty rather than an exception, because a surface failing to draw is worse than a surface drawing less.
    """
    severity = str(row.get("severity", "important") or "important")
    return Finding(
        path=str(row.get("path", "") or ""),
        line=int(row.get("line", 0) or 0),
        severity=severity,
        title=str(row.get("title", "") or row.get("summary", "") or "(untitled finding)"),
        why=str(row.get("why", "") or row.get("detail", "") or ""),
        evidence=str(row.get("evidence", "") or ""),
    )


def _findings_from_cache(state_dir: Path, repo: str, pr: int, head: str) -> list[Finding]:
    """Findings from the cached review at a head, if there is one.

    The cache holds the posted payload, which carries more detail than the state does (why, evidence,
    severity). It is a cache, so it may be absent — that is not an error.
    """
    if not head:
        return []
    cached = ReviewCache(state_dir).get(repo, head)
    if not isinstance(cached, dict):
        return []
    rows = cached.get("findings")
    if not isinstance(rows, list):
        return []
    return [_finding_from_dict(row) for row in rows if isinstance(row, dict)]


def load_snapshot(repo: str, pr: int, *, state_dir: Path | str | None = None) -> StoreSnapshot:
    """Read one pull request's stored review. Never raises for missing state."""
    root = Path(state_dir) if state_dir else default_state_dir()
    snapshot = StoreSnapshot(repo=repo, pr=int(pr))

    state = ReviewStore(root).load(repo, int(pr))
    if not state.has_baseline and not state.reviews and not state.open_all:
        # Nothing has ever been written for this pull request. Say so rather than draw a clean board.
        return snapshot

    snapshot.found = True
    snapshot.reviewed_head = state.last_reviewed_head
    snapshot.head = state.last_reviewed_head
    snapshot.complete = bool(state.last_reviewed_head)
    snapshot.paused = bool(state.paused)
    snapshot.pause_reason = str(state.pause_reason or "")
    snapshot.reviews = int(state.reviews or 0)
    snapshot.last_review_at = float(state.last_review_at or 0)
    snapshot.title = str(state.title or "")

    # Prefer the cache for detail; fall back to the union of open findings in state, which is what the
    # ledger of record actually holds.
    findings = _findings_from_cache(root, repo, int(pr), state.last_reviewed_head)
    if not findings:
        findings = [_finding_from_dict(row) for row in state.open_all.values()
                    if isinstance(row, dict)]
    snapshot.findings = findings

    tracked = Ledger(root).open_entries(repo, int(pr))
    snapshot.tracked = [entry.as_dict() for entry in tracked]
    return snapshot


def build_projection(snapshot: StoreSnapshot, *, base_ref: str = "", branch: str = "",
                     head: str = "") -> Projection:
    """The projection for a snapshot, via `build_from_review`.

    Shows the stored head when nothing fresher is supplied: the verdict is *about* that head, and
    pretending otherwise is how a stale verdict gets read as current.
    """
    projection = build_from_review(
        repo=snapshot.repo,
        pr=snapshot.pr,
        # No stored title: leave it empty rather than repeating the number as a title. A surface can
        # then show "#25" alone, which is honest, instead of "#25 #25".
        title=snapshot.title,
        head=head or snapshot.head,
        reviewed_head=snapshot.reviewed_head,
        findings=snapshot.findings,
        complete=snapshot.complete,
        paused_reason=snapshot.pause_reason if snapshot.paused else "",
        # Not stored, so reported as unknown rather than guessed. See the module docstring.
        approvals_state="",
        ci_state="",
        policy_state="",
        profile="standard",
        runtime_owner="",
    )
    projection.branch = branch
    projection.base_ref = base_ref
    if snapshot.tracked:
        for finding in projection.findings:
            names = [row.get("title", "") for row in snapshot.tracked
                     if row.get("path") == finding.path]
            if names:
                finding.also_noted = tuple(n for n in names if n and n != finding.title) or ()
    return projection


def load_projection(repo: str, pr: int, *, state_dir: Path | str | None = None,
                    base_ref: str = "", branch: str = "",
                    head: str = "", with_inbox: bool = True) -> Projection:
    """The one entry point: stored state in, projection out.

    `with_inbox` fills the board's left pane from every stored review, so the board shows what else
    exists rather than a single row in an otherwise empty list. It reads only the state dir, never the
    network.
    """
    snapshot = load_snapshot(repo, pr, state_dir=state_dir)
    projection = build_projection(snapshot, base_ref=base_ref, branch=branch, head=head)
    if with_inbox:
        # `with_inbox=False` below is load-bearing: building the rows calls back into this function,
        # and without the flag that is unbounded recursion. It cost a RecursionError to find.
        rows = rows_for_state_dir(state_dir)
        if rows:
            projection.rows = rows
    return projection


def discover(state_dir: Path | str | None = None) -> list[tuple[str, int]]:
    """Every (repo, pr) with stored review state, newest first.

    `status` used to invent a board of fixtures. It now lists what has actually been reviewed, which is
    the only answer to "what is being watched" that can be true.
    """
    root = Path(state_dir) if state_dir else default_state_dir()
    store_root = root / "review-state"
    if not store_root.is_dir():
        return []
    found: list[tuple[float, str, int]] = []
    for path in store_root.glob("*.json"):
        if path.name.endswith(".tmp"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A torn file is skipped, not fatal: one unreadable entry must not empty the whole list.
            continue
        if not isinstance(data, dict):
            continue
        repo = str(data.get("repo", "") or "")
        pr = int(data.get("pr", 0) or 0)
        if not repo or not pr:
            # Older files were named but did not carry the fields; recover from the filename.
            stem = path.stem
            if "-" in stem:
                repo_part, _, pr_part = stem.rpartition("-")
                repo = repo or repo_part.replace("__", "/")
                try:
                    pr = pr or int(pr_part)
                except ValueError:
                    continue
        if not repo or not pr:
            continue
        found.append((float(data.get("last_review_at", 0) or 0), repo, pr))
    found.sort(reverse=True)
    return [(repo, pr) for _at, repo, pr in found]


def rows_for_state_dir(state_dir: Path | str | None = None) -> list[Row]:
    """The inbox rows for everything reviewed, for `status` and the board's left pane."""
    rows: list[Row] = []
    for repo, pr in discover(state_dir):
        # No inbox: this function *is* the inbox. Recursing here would never terminate.
        projection = load_projection(repo, pr, state_dir=state_dir, with_inbox=False)
        rows.append(Row(
            repo=repo, pr=pr, title=projection.title, head=projection.head,
            state=projection.state,
            # The short reason, which the surface shows as-is. A headline clipped mid-word reads as a
            # rendering fault, which is why this is not `next_bar()[0]`.
            reason=short_reason(projection),
            needs_human=projection.state in ("findings", "approvals-blocked", "incomplete", "error"),
            age_seconds=0,
        ))
    return rows


def short_reason(projection: Projection) -> str:
    if projection.state == "incomplete":
        return "Review incomplete: not a pass"
    if projection.state == "findings":
        count = len([f for f in projection.findings
                     if f.severity.lower() in ("important", "blocking")])
        return f"Fix {count} important finding{'' if count == 1 else 's'}"
    if projection.state == "approvals-blocked":
        return "Waiting for approvals"
    if projection.state == "approvals-satisfied":
        return "Rules met"
    if projection.state == "paused":
        return "Paused at the review cap"
    if projection.state == "error":
        return projection.refusal or "The review could not run"
    return projection.chip
