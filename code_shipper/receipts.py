"""D16e: receipts are kept 30 days per repository, and never pruned when they still matter.

Receipts are the record reconciliation relies on. Pruning one for an open PR, or for a write whose outcome
is unknown, would let that write be blindly replayed — the failure the whole receipt mechanism exists to
prevent. So pruning is age-based *and* bounded by two exceptions that always win:

* a receipt for an open PR is kept, whatever its age;
* a receipt for an unreconciled or uncertain write is kept, whatever its age.

Retention is per repository: pruning one never touches another. Pruning runs at the start of a command, not
as a daemon, and every pruning is logged so a deletion is auditable rather than silent.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

RETENTION_DAYS = 30
_KEEP_STATUSES = frozenset({"open", "uncertain", "unreconciled", "pending"})


@dataclass(frozen=True)
class PruneResult:
    """What was removed and what was deliberately kept, with the reason for each keep."""

    removed: tuple[str, ...] = ()
    kept_protected: tuple[tuple[str, str], ...] = ()
    kept_young: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "removed": list(self.removed),
            "kept_protected": [{"receipt": name, "reason": reason} for name, reason in self.kept_protected],
            "kept_young": self.kept_young,
        }

    @property
    def removed_count(self) -> int:
        return len(self.removed)


def _receipt_age_days(path: str, now: float) -> float:
    return (now - os.path.getmtime(path)) / 86400.0


def _receipt_protection(data: dict) -> str:
    """Why this receipt must not be pruned, or "" when it may be considered.

    Read from the receipt's own recorded state, not inferred from the file name: a name can lag a state
    change, and getting this wrong loses the record reconciliation needs.
    """
    pr = data.get("pr")
    pr = pr if isinstance(pr, dict) else {}
    status = str(pr.get("state", data.get("pr_state", "")) or "").lower()
    if status in _KEEP_STATUSES:
        return f"PR is {status}"

    push = data.get("push")
    push = push if isinstance(push, dict) else {}
    verdict = str(push.get("verdict", data.get("verdict", "")) or "").lower()
    if verdict in _KEEP_STATUSES or "uncertain" in verdict or "unreconciled" in verdict:
        return f"write outcome is {verdict or 'not recorded'}"

    if data.get("needs_reconciliation") or push.get("needs_reconciliation"):
        return "write needs reconciliation"
    return ""


def prune_repo_receipts(
    *, receipts_dir: str, repo: str, now: float | None = None, retention_days: int = RETENTION_DAYS
) -> PruneResult:
    """Prune one repository's old receipts, keeping every one that still matters.

    `repo` scopes the walk: another repository's directory is never entered, which is the per-repo guarantee
    rather than a consequence of the layout.
    """
    now = time.time() if now is None else now
    root = os.path.join(receipts_dir, repo.replace("/", "__"))
    if not os.path.isdir(root):
        return PruneResult()

    removed: list[str] = []
    protected: list[tuple[str, str]] = []
    young = 0

    for name in sorted(os.listdir(root)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        if _receipt_age_days(path, now) < retention_days:
            young += 1
            continue

        try:
            with open(path) as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            # Unreadable is not the same as disposable: a receipt we cannot parse cannot be shown to be
            # safe to delete, so it is kept and reported.
            protected.append((name, "could not be read, so it cannot be shown to be safe to delete"))
            continue

        reason = _receipt_protection(data if isinstance(data, dict) else {})
        if reason:
            protected.append((name, reason))
            continue

        os.unlink(path)
        removed.append(name)

    return PruneResult(removed=tuple(removed), kept_protected=tuple(protected), kept_young=young)


def prune_all(
    *, receipts_dir: str, now: float | None = None, retention_days: int = RETENTION_DAYS,
    log: list[str] | None = None,
) -> dict[str, PruneResult]:
    """Prune every repository that has receipts. Returns one result per repository."""
    now = time.time() if now is None else now
    results: dict[str, PruneResult] = {}
    if not os.path.isdir(receipts_dir):
        return results
    for entry in sorted(os.listdir(receipts_dir)):
        full = os.path.join(receipts_dir, entry)
        if not os.path.isdir(full):
            continue
        repo = entry.replace("__", "/")
        results[repo] = prune_repo_receipts(
            receipts_dir=receipts_dir, repo=repo, now=now, retention_days=retention_days
        )
    if log is not None:
        for repo, result in results.items():
            if result.removed or result.kept_protected:
                log.append(
                    f"receipts {repo}: pruned {result.removed_count}, "
                    f"kept {len(result.kept_protected)} protected, {result.kept_young} within retention"
                )
    return results
