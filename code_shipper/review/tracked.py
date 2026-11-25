"""Tracked findings: a finding a maintainer asks us to keep an eye on (C11).

The flow: a write-access user comments `@code-shipper track <finding-id>`; later reviews check new
commits against that finding and reply when it looks satisfied. Three properties matter.

**Authority comes from the host API, never the comment text.** The same rule as command handling in
P3-C: a body claiming "(maintainer, write access)" from someone with `none` permission is ignored. The
permission is read from GitHub for the comment's author.

**Capped at 20.** A cap is not a nicety: an uncapped list becomes a second review backlog nobody
reads, and each entry costs a check on every later review. When the cap is reached, the refusal is
explicit rather than a silent drop.

**Stored as a ledger entry with actor and timestamp.** "Who asked for this, and when" is the question
asked when a tracked finding later blocks something — and it cannot be reconstructed from a comment
that may since have been edited or deleted.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The cap, named so a test and a report can both cite the same number.
MAX_TRACKED = 20

# Permissions that may create or close a tracked finding. Read-only and triage are excluded: triage can
# label a PR, which is not the same as directing review attention.
WRITE_PERMISSIONS = ("admin", "maintain", "write")

# Deterministic, stable identity for a finding, independent of any single review run.
def finding_key(*, path: str, title: str) -> str:
    digest = hashlib.sha256(f"{path}|{title}".encode()).hexdigest()[:10]
    return f"T-{digest}"


@dataclass
class Tracked:
    """One tracked finding."""

    key: str
    path: str
    title: str
    actor: str
    created_at: float = 0.0
    head_at_creation: str = ""
    state: str = "open"          # open | satisfied | closed
    satisfied_at: float = 0.0
    satisfied_at_head: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = time.time()

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "path": self.path,
            "title": self.title,
            "actor": self.actor,
            "created_at": self.created_at,
            "head_at_creation": self.head_at_creation,
            "state": self.state,
            "satisfied_at": self.satisfied_at,
            "satisfied_at_head": self.satisfied_at_head,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Tracked":
        return cls(
            key=str(data.get("key", "")),
            path=str(data.get("path", "")),
            title=str(data.get("title", "")),
            actor=str(data.get("actor", "")),
            created_at=float(data.get("created_at", 0) or 0),
            head_at_creation=str(data.get("head_at_creation", "")),
            state=str(data.get("state", "open")),
            satisfied_at=float(data.get("satisfied_at", 0) or 0),
            satisfied_at_head=str(data.get("satisfied_at_head", "")),
            note=str(data.get("note", "")),
        )


@dataclass
class Ledger:
    """The append-only record. One JSON file per repository, under the state dir."""

    root: Path

    def _path(self, repo: str) -> Path:
        safe = repo.replace("/", "__")
        return Path(self.root) / "tracked" / f"{safe}.json"

    def _read(self, repo: str) -> dict[str, Any]:
        try:
            data = json.loads(self._path(repo).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"entries": [], "events": []}
        if not isinstance(data, dict):
            return {"entries": [], "events": []}
        data.setdefault("entries", [])
        data.setdefault("events", [])
        return data

    def _write(self, repo: str, data: dict[str, Any]) -> None:
        path = self._path(repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def entries(self, repo: str, pr: int) -> list[Tracked]:
        rows = self._read(repo).get("entries", [])
        return [
            Tracked.from_dict(row) for row in rows
            if isinstance(row, dict) and int(row.get("pr", 0) or 0) == int(pr)
        ]

    def open_entries(self, repo: str, pr: int) -> list[Tracked]:
        return [entry for entry in self.entries(repo, pr) if entry.state == "open"]

    def events(self, repo: str, pr: int) -> list[dict[str, Any]]:
        return [
            event for event in self._read(repo).get("events", [])
            if isinstance(event, dict) and int(event.get("pr", 0) or 0) == int(pr)
        ]

    def add(self, repo: str, pr: int, entry: Tracked) -> Tracked:
        """Add one entry, enforcing the cap. Raises ``TrackingRefused`` at the cap."""
        data = self._read(repo)
        current = [
            row for row in data["entries"]
            if isinstance(row, dict) and int(row.get("pr", 0) or 0) == int(pr)
            and str(row.get("state", "open")) == "open"
        ]
        for row in current:
            if str(row.get("key", "")) == entry.key:
                raise TrackingRefused(
                    "already_tracked", f"{entry.key} is already tracked on this pull request"
                )
        if len(current) >= MAX_TRACKED:
            raise TrackingRefused(
                "cap_reached",
                f"{MAX_TRACKED} findings are already tracked on this pull request; close one first",
            )
        data["entries"].append({"pr": int(pr), **entry.as_dict()})
        data["events"].append({
            "pr": int(pr), "action": "track", "key": entry.key, "actor": entry.actor,
            "at": entry.created_at, "head": entry.head_at_creation,
        })
        self._write(repo, data)
        return entry

    def mark(self, repo: str, pr: int, key: str, *, state: str, head: str = "",
             note: str = "", actor: str = "code-shipper") -> Tracked | None:
        data = self._read(repo)
        found: Tracked | None = None
        for row in data["entries"]:
            if not isinstance(row, dict):
                continue
            if int(row.get("pr", 0) or 0) != int(pr) or str(row.get("key", "")) != key:
                continue
            row["state"] = state
            if state == "satisfied":
                row["satisfied_at"] = time.time()
                row["satisfied_at_head"] = head
            if note:
                row["note"] = note
            found = Tracked.from_dict(row)
        if found is not None:
            data["events"].append({
                "pr": int(pr), "action": state, "key": key, "actor": actor,
                "at": time.time(), "head": head,
            })
            self._write(repo, data)
        return found


class TrackingRefused(Exception):
    """A tracking request that cannot proceed. ``code`` is stable."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# -- command parsing -------------------------------------------------------


@dataclass
class TrackCommand:
    action: str        # track | untrack
    target: str
    raw: str = ""


def parse_command(body: str) -> TrackCommand | None:
    """`@code-shipper track T-abc123` (or `untrack`). Returns None if this is not a command."""
    for line in (body or "").splitlines():
        lowered = line.strip().lower()
        if not lowered.startswith("@code-shipper"):
            continue
        rest = line.strip()[len("@code-shipper"):].strip()
        parts = rest.split()
        if len(parts) >= 2 and parts[0].lower() in ("track", "untrack"):
            return TrackCommand(parts[0].lower(), parts[1], raw=line.strip())
    return None


def authorise(*, permission: str) -> None:
    """Refuse unless the host says the actor can direct review attention.

    The permission is a parameter, not something read from the comment: the text is untrusted, the
    host's answer is not.
    """
    if (permission or "none").strip().lower() not in WRITE_PERMISSIONS:
        raise TrackingRefused(
            "not_authorised",
            f"permission {permission or 'none'!r} cannot track findings; "
            f"{', '.join(WRITE_PERMISSIONS)} required",
        )


# -- checking later reviews ------------------------------------------------


@dataclass
class CheckResult:
    key: str
    satisfied: bool
    reason: str
    evidence_path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "satisfied": self.satisfied, "reason": self.reason,
                "evidence_path": self.evidence_path}


def _file_removed(tracked: Tracked, new_files: dict[str, str]) -> bool:
    return tracked.path not in new_files


def _title_gone(tracked: Tracked, findings: list[Any]) -> bool:
    """Whether the finding itself has stopped being reported at the new head."""
    for finding in findings:
        if getattr(finding, "path", "") != tracked.path:
            continue
        if str(getattr(finding, "title", "")).strip().lower() == tracked.title.strip().lower():
            return False
    return True


def check(
    entries: list[Tracked],
    *,
    new_head: str,
    new_files: dict[str, str],
    findings: list[Any],
    touched_paths: set[str] | None = None,
) -> list[CheckResult]:
    """Check open tracked findings against the new head.

    A tracked finding is *satisfied* when either the file it named no longer exists, or the finding is
    no longer reported at the new head. It is **not** satisfied merely because commits arrived: a push
    that touches an unrelated file proves nothing about this one, and saying "satisfied" after any push
    would train a maintainer to ignore the reply.

    When the finding is still reported, the reason says so plainly, which is the more useful outcome:
    the maintainer learns the fix did not land.
    """
    results: list[CheckResult] = []
    touched = touched_paths or set()
    for entry in entries:
        if entry.state != "open":
            continue
        if _file_removed(entry, new_files):
            results.append(CheckResult(
                entry.key, True, f"{entry.path} no longer exists at this head", entry.path
            ))
            continue
        if _title_gone(entry, findings):
            # Only credible if the file was actually part of this review's scope; otherwise the
            # finding may simply not have been looked at.
            if touched and entry.path not in touched:
                results.append(CheckResult(
                    entry.key, False,
                    f"{entry.path} was not in this review's scope, so absence of the finding "
                    "is not evidence it is fixed",
                    entry.path,
                ))
                continue
            results.append(CheckResult(
                entry.key, True, f"no longer reported at {new_head[:8]}", entry.path
            ))
            continue
        results.append(CheckResult(
            entry.key, False, f"still reported at {new_head[:8]}", entry.path
        ))
    return results


def reply_body(results: list[CheckResult], entries: list[Tracked], *, head: str) -> str:
    """The one reply posted when a tracked finding changes state.

    Returns "" when nothing changed, so the caller posts nothing: a reply on every push is noise that
    makes the informative replies invisible.
    """
    satisfied = [result for result in results if result.satisfied]
    if not satisfied:
        return ""
    by_key = {entry.key: entry for entry in entries}
    lines = [
        "## Tracked findings",
        "",
        f"Checked at {head[:8]}. {len(satisfied)} of {len(results)} tracked finding(s) look "
        "addressed:",
        "",
    ]
    for result in satisfied:
        entry = by_key.get(result.key)
        title = entry.title if entry else result.key
        lines.append(f"- `{result.key}` {title} — {result.reason}")
    still = [result for result in results if not result.satisfied]
    if still:
        lines += ["", "Still open:", ""]
        for result in still:
            entry = by_key.get(result.key)
            title = entry.title if entry else result.key
            lines.append(f"- `{result.key}` {title} — {result.reason}")
    lines += [
        "",
        "_A tracked finding is marked satisfied when it is no longer reported at this head, or its "
        "file no longer exists. That is evidence, not proof: review again before merging._",
    ]
    return "\n".join(lines)
