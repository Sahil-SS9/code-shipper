"""Review cache keyed on (repo, head_sha) — C20.

A review of a head we have already reviewed should not cost a model call. The cache is deliberately
keyed on the **head SHA**, not on a PR number: a PR whose head moved has genuinely different content,
and serving the previous review would be the worst kind of stale — plausible, confident, and about
code that is no longer there.

A corrupt or unreadable entry is a miss, never a crash: a cache is an optimisation, and an
optimisation that can fail a review is not worth having.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


class ReviewCache:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root) / "review-cache"

    def _path(self, repo: str, head_sha: str) -> Path:
        key = hashlib.sha256(f"{repo}\x1f{head_sha}".encode()).hexdigest()[:24]
        safe_head = "".join(c for c in head_sha if c.isalnum())[:12]
        return self.root / f"{safe_head}-{key}.json"

    def get(self, repo: str, head_sha: str) -> dict[str, Any] | None:
        path = self._path(repo, head_sha)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return payload.get("review")

    def put(self, repo: str, head_sha: str, review: dict[str, Any]) -> None:
        path = self._path(repo, head_sha)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"repo": repo, "head_sha": head_sha, "cached_at": time.time(),
                            "review": review}, sort_keys=True),
                encoding="utf-8",
            )
        except OSError:
            # A cache write that fails is not a review failure.
            pass

    def clear(self, repo: str | None = None) -> int:
        removed = 0
        for path in self.root.glob("*.json") if self.root.exists() else []:
            try:
                if repo is not None:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    if payload.get("repo") != repo:
                        continue
                path.unlink()
                removed += 1
            except (OSError, json.JSONDecodeError):
                continue
        return removed
