"""N2: three tries per problem, then stop and hand it back.

The budget caps a whole pull request (rounds, pushes, wall clock). That still let a watch spend every
round on the same failing check. Anthropic's watch pushes at most three times for one issue, then lists
what it tried and hands back to the author; this is the same rule.

A *problem* is one work item, keyed by its kind and its source (the check name, the comment id, the
finding fingerprint). Every round that works a problem counts as one attempt, whether it pushed or not:
a push that did not make the problem go away is a failed attempt, which shows up as the same problem
being offered again. When a problem is offered a fourth time it is not worked; it is reported as
"needs you" with the attempts listed.

The log is always on. `watch()` builds an in-memory one when the caller passes none, so the cap cannot be
lost by forgetting an argument; the CLI passes a file-backed one so the count survives between watches.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..atomic import read_json, write_json_atomic

MAX_ATTEMPTS_PER_PROBLEM = 3


def problem_key(item: Any) -> str:
    """Stable identity for one problem across rounds and across watches."""
    kind = str(getattr(item, "kind", "") or "unknown")
    source = str(getattr(item, "source_id", "") or "")
    if not source:
        reason = str(getattr(item, "reason", "") or "")
        source = "reason:" + hashlib.sha256(reason.encode("utf-8")).hexdigest()[:12]
    return f"{kind}:{source}"


@dataclass
class AttemptLog:
    """Attempts per problem. File-backed when `path` is set, in memory otherwise."""

    path: Path | None = None
    problems: dict[str, dict[str, Any]] = field(default_factory=dict)
    limit: int = MAX_ATTEMPTS_PER_PROBLEM

    @classmethod
    def load(cls, path: Path, *, limit: int = MAX_ATTEMPTS_PER_PROBLEM) -> "AttemptLog":
        data = read_json(path, default=None)
        problems = data.get("problems", {}) if isinstance(data, dict) else {}
        if not isinstance(problems, dict):
            problems = {}
        return cls(path=path, problems=problems, limit=limit)

    def save(self) -> None:
        if self.path is not None:
            write_json_atomic(self.path, {"problems": self.problems})

    def attempts(self, key: str) -> list[dict[str, Any]]:
        entry = self.problems.get(key) or {}
        found = entry.get("attempts")
        return list(found) if isinstance(found, list) else []

    def exhausted(self, key: str) -> bool:
        return len(self.attempts(key)) >= self.limit

    def record(self, item: Any, *, verdict: str, detail: str = "", now: float | None = None) -> None:
        key = problem_key(item)
        entry = self.problems.setdefault(
            key, {"kind": str(getattr(item, "kind", "")), "reason": str(getattr(item, "reason", "")),
                  "attempts": []}
        )
        entry.setdefault("attempts", []).append(
            {"ts": time.time() if now is None else now, "verdict": verdict, "detail": detail[:300]}
        )
        self.save()

    def reported(self, key: str) -> bool:
        return bool((self.problems.get(key) or {}).get("reported"))

    def mark_reported(self, key: str) -> None:
        self.problems.setdefault(key, {"attempts": []})["reported"] = True
        self.save()


def needs_you_comment(entries: list[dict[str, Any]]) -> str:
    """One comment for everything handed back in this watch. Plain, and lists what was tried."""
    lines = ["**Code-Shipper: needs you**", ""]
    for entry in entries:
        lines.append(f"I tried {len(entry['attempts'])} times and stopped working on: {entry['reason']}")
        for number, attempt in enumerate(entry["attempts"], 1):
            detail = f" — {attempt['detail']}" if attempt.get("detail") else ""
            lines.append(f"{number}. {attempt.get('verdict', '?')}{detail}")
        lines.append("")
    lines.append("I will not try these again on this pull request. Advisory. Code-Shipper never approves "
                 "or merges.")
    return "\n".join(lines)
