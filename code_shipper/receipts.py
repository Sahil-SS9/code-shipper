"""D16e: old ledger records are pruned; anything that still matters never is.

The first version walked `<state>/receipts` — a directory nothing in this codebase writes — and decided
protection from fields no record has (`pr.state`, `push.verdict`). It pruned nothing in practice, and would
have pruned the wrong things if it had. This version reads the records that actually exist: the broker
ledger, one append-only JSON-lines file per pull request at
`<state>/prs/<host>/<owner>/<repo>/<number>.jsonl`.

The ledger's real vocabulary is `planned`, `posting`, `confirmed`, `refused`, `uncertain`, carried in
`outcome` on `write` entries. Retention follows from what those words mean:

* `planned`, `posting`, `uncertain` — the write may yet land, or may already have landed. Deleting the
  record would let it be replayed blind, which is the exact failure the ledger exists to prevent. **Kept,
  whatever the age.**
* an unrecognised or absent outcome on a write — **kept.** A record whose state cannot be read cannot be
  shown to be safe to delete, so the rule fails closed rather than tidy.
* no usable timestamp — **kept**, for the same reason: age cannot be established.
* `confirmed` or `refused`, older than the window, for a pull request the caller has told us is closed —
  **pruned.** Nothing can happen to a settled write on a closed pull request.

Nothing here contacts GitHub. Whether a pull request is closed is an input (`closed_prs`), because a pruner
that made network calls at the start of every command would add latency and failure modes to the command the
user actually asked for. A pull request not named in that set is treated as open, so the default is keep.

The CLI supplies that set by asking only about `closure_candidates` — ledgers that are old and settled, so
the answer can actually change something — through `resolve_closed`, which is capped and treats every
error as "not closed". With no such ledgers, no request is made.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

RETENTION_DAYS = 30

# Outcomes that must never be pruned: the write is not settled, so its record is still load-bearing.
OPEN_OUTCOMES = frozenset({"planned", "posting", "uncertain"})
# Outcomes that may be pruned once old enough, and only for a closed pull request.
SETTLED_OUTCOMES = frozenset({"confirmed", "refused"})


@dataclass(frozen=True)
class PruneResult:
    """What was removed and what was deliberately kept, with the reason for each keep."""

    removed: tuple[str, ...] = ()
    kept_protected: tuple[tuple[str, str], ...] = ()
    kept_young: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "removed": list(self.removed),
            "kept_protected": [{"record": name, "reason": reason} for name, reason in self.kept_protected],
            "kept_young": self.kept_young,
        }

    @property
    def removed_count(self) -> int:
        return len(self.removed)


def _safe_component(value: str, label: str) -> str:
    """Reject anything that could walk out of the state tree.

    Owner and repository names reach the filesystem here. The ledger validates them on the write path; this
    is the second door, on the pruning path, so a crafted name cannot become a traversal.
    """
    if not value or value in (".", "..") or "/" in value or "\\" in value or "\x00" in value:
        raise ValueError(f"unsafe {label}: {value!r}")
    return value


def _read_entries(path: str) -> list[dict] | None:
    """Every parseable entry in one ledger file, or None when the file cannot be read at all.

    A single unparseable *line* is skipped: the ledger's own reader does the same, because a crash can lose
    the line in flight. A file that cannot be opened is different — the record cannot be examined, so the
    caller must keep it.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            raw_lines = handle.readlines()
    except (OSError, UnicodeDecodeError):
        # UnicodeDecodeError is not an OSError. Found by a test that wrote invalid bytes: the walk raised
        # out of the prune and would have aborted the command it was housekeeping for.
        return None

    entries: list[dict] = []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
    return entries


def _newest_ts(entries: list[dict]) -> float | None:
    newest: float | None = None
    for entry in entries:
        ts = entry.get("ts")
        if isinstance(ts, (int, float)):
            newest = float(ts) if newest is None else max(newest, float(ts))
    return newest


def _protection(entries: list[dict] | None) -> str:
    """Why this pull request's ledger must be kept, or "" when it may be considered for pruning.

    Reads only what the ledger actually records. A `write` entry with no outcome is not treated as settled:
    the absence of a verdict is exactly the state that must not be pruned.
    """
    if entries is None:
        return "the file could not be read, so it cannot be shown to be safe to delete"
    if not entries:
        return "no readable entries, so its state is unknown"

    for entry in entries:
        if entry.get("kind") != "write":
            # Only write entries carry an outcome. event/decision/agent_run/budget say nothing about whether
            # a write is settled, so they are neither evidence of openness nor of settlement.
            continue
        outcome = entry.get("outcome")
        if not isinstance(outcome, str) or not outcome:
            return "a write has no recorded outcome, so its state is unknown"
        if outcome in OPEN_OUTCOMES:
            return f"a write is {outcome}, so it may still land"
        if outcome not in SETTLED_OUTCOMES:
            return f"a write has an unrecognised outcome {outcome!r}, so its state is unknown"
    return ""


def prune_pr_ledger(
    *,
    path: str,
    closed: bool,
    now: float | None = None,
    retention_days: int = RETENTION_DAYS,
) -> PruneResult:
    """Prune one pull request's ledger if, and only if, it is safe to.

    `closed` must come from a caller that has seen the pull request's state on GitHub. The default posture is
    to keep: a pull request whose state is unknown is passed as not closed.
    """
    now = time.time() if now is None else now
    if not os.path.isfile(path):
        return PruneResult()

    name = os.path.basename(path)
    entries = _read_entries(path)
    reason = _protection(entries)
    if reason:
        return PruneResult(kept_protected=((name, reason),))

    assert entries is not None  # _protection only returns "" for a readable, non-empty list
    newest = _newest_ts(entries)
    if newest is None:
        return PruneResult(
            kept_protected=((name, "no entry carries a timestamp, so the record's age cannot be established"),)
        )

    # Exactly at the window is still inside it. Measured: with `<`, a record aged exactly 30 days was
    # pruned, which nobody asking for "kept for 30 days" would expect.
    if (now - newest) / 86400.0 <= retention_days:
        return PruneResult(kept_young=1)

    if not closed:
        return PruneResult(
            kept_protected=((name, "the pull request is not known to be closed"),)
        )

    os.unlink(path)
    return PruneResult(removed=(name,))


def prune_repo_ledgers(
    *,
    state_dir: str,
    owner: str,
    repo: str,
    closed_prs: set[int] | frozenset[int] = frozenset(),
    now: float | None = None,
    retention_days: int = RETENTION_DAYS,
    host: str = "github.com",
) -> dict[int, PruneResult]:
    """Prune one repository's ledgers. Another repository's directory is never entered.

    Per-repository is a property of the walk rather than a consequence of the layout, so a bug here cannot
    reach a different repository's records.
    """
    now = time.time() if now is None else now
    _safe_component(owner, "owner")
    _safe_component(repo, "repo")
    _safe_component(host, "host")

    root = os.path.join(state_dir, "prs", host, owner, repo)
    if not os.path.isdir(root):
        return {}

    results: dict[int, PruneResult] = {}
    for name in sorted(os.listdir(root)):
        if not name.endswith(".jsonl"):
            continue
        stem = name[: -len(".jsonl")]
        if not stem.isdigit():
            continue
        number = int(stem)
        results[number] = prune_pr_ledger(
            path=os.path.join(root, name),
            closed=number in closed_prs,
            now=now,
            retention_days=retention_days,
        )
    return results


def _walk_ledgers(state_dir: str, host: str):
    """Yield ("owner/repo", number, path) for every per-PR ledger under the state tree."""
    host_root = os.path.join(state_dir, "prs", host)
    if not os.path.isdir(host_root):
        return
    for owner in sorted(os.listdir(host_root)):
        owner_root = os.path.join(host_root, owner)
        if not os.path.isdir(owner_root):
            continue
        for repo in sorted(os.listdir(owner_root)):
            repo_root = os.path.join(owner_root, repo)
            if not os.path.isdir(repo_root):
                continue
            for name in sorted(os.listdir(repo_root)):
                stem = name[: -len(".jsonl")] if name.endswith(".jsonl") else ""
                if stem.isdigit():
                    yield f"{owner}/{repo}", int(stem), os.path.join(repo_root, name)


def closure_candidates(
    *,
    state_dir: str,
    now: float | None = None,
    retention_days: int = RETENTION_DAYS,
    host: str = "github.com",
) -> list[tuple[str, int]]:
    """Pull requests whose ledger would be pruned *if* the pull request were closed.

    Only these are worth asking GitHub about: a ledger that is young, unsettled or unreadable is kept
    whatever the answer, so asking about it would be a network call that cannot change anything. On a
    normal day this list is empty and the command makes no extra request at all.
    """
    now = time.time() if now is None else now
    found: list[tuple[str, int]] = []
    for key, number, path in _walk_ledgers(state_dir, host):
        entries = _read_entries(path)
        if _protection(entries):
            continue
        newest = _newest_ts(entries or [])
        if newest is None or (now - newest) / 86400.0 <= retention_days:
            continue
        found.append((key, number))
    return found


def resolve_closed(
    candidates: list[tuple[str, int]],
    get_state,
    *,
    cap: int = 20,
    log: list[str] | None = None,
) -> dict[str, set[int]]:
    """Ask the host which candidates are closed. Best effort, and every doubt resolves to "keep".

    `get_state(repo, number)` returns the pull request's state string. Any exception, any answer other
    than exactly "closed", and anything past `cap` leaves the pull request out of the closed set, so its
    record is kept and is simply considered again on a later run. The cap bounds how long housekeeping can
    hold up the command the user asked for.
    """
    closed: dict[str, set[int]] = {}
    asked = 0
    for key, number in candidates:
        if asked >= cap:
            if log is not None:
                log.append(f"closure check capped at {cap}; {len(candidates) - cap} left for a later run")
            break
        asked += 1
        try:
            state = get_state(key, number)
        except Exception as exc:  # noqa: BLE001 - an unanswered question means keep
            if log is not None:
                log.append(f"{key}#{number}: state unknown ({exc}), kept")
            continue
        if state == "closed":
            closed.setdefault(key, set()).add(number)
    return closed


def prune_all(
    *,
    state_dir: str,
    closed_prs_by_repo: dict[str, set[int]] | None = None,
    now: float | None = None,
    retention_days: int = RETENTION_DAYS,
    log: list[str] | None = None,
    host: str = "github.com",
) -> dict[str, dict[int, PruneResult]]:
    """Prune every repository under the state tree. Returns one map per repository.

    Repositories named in `closed_prs_by_repo` ("owner/repo" -> closed numbers) get their settled, old
    records pruned. Every other repository is walked but keeps everything, because nothing has told us its
    pull requests are closed.
    """
    now = time.time() if now is None else now
    closed_prs_by_repo = closed_prs_by_repo or {}
    results: dict[str, dict[int, PruneResult]] = {}

    host_root = os.path.join(state_dir, "prs", host)
    if not os.path.isdir(host_root):
        return results

    for owner in sorted(os.listdir(host_root)):
        owner_root = os.path.join(host_root, owner)
        if not os.path.isdir(owner_root):
            continue
        for repo in sorted(os.listdir(owner_root)):
            if not os.path.isdir(os.path.join(owner_root, repo)):
                continue
            key = f"{owner}/{repo}"
            per_pr = prune_repo_ledgers(
                state_dir=state_dir, owner=owner, repo=repo,
                closed_prs=closed_prs_by_repo.get(key, frozenset()),
                now=now, retention_days=retention_days, host=host,
            )
            if not per_pr:
                continue
            results[key] = per_pr
            removed = sum(r.removed_count for r in per_pr.values())
            protected = sum(len(r.kept_protected) for r in per_pr.values())
            if log is not None and (removed or protected):
                log.append(f"{key}: pruned {removed}, kept {protected} for a reason")
    return results
