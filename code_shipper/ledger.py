"""Per-pull-request ledger: append-only JSON lines, plus a per-PR lock.

The ledger is the record of what Code-Shipper did and what it was told. Two properties matter:

* **Append-only.** A line is written, flushed and fsynced. Nothing is rewritten in place, so a crash
  loses at most the line in flight and never corrupts what came before.
* **Uncertain is a state, not a gap.** A write that may or may not have reached GitHub is recorded as
  ``uncertain`` and never replayed blindly; the next tick reads GitHub to settle it. That is what
  makes a timeout safe rather than a duplicate comment.

The lock is an OS-level advisory lock on a per-PR file. It is released by the kernel if the process
dies, which a "lock file exists" check cannot do.
"""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

KINDS = frozenset({"event", "decision", "write", "agent_run", "budget"})
OUTCOMES = frozenset({"planned", "posting", "confirmed", "refused", "uncertain"})
MAX_LINE_BYTES = 256 * 1024

_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")


class LedgerError(RuntimeError):
    """The ledger could not be read or written safely."""


def _check_component(value: str, label: str) -> str:
    if not value or not _SAFE.match(value) or value in (".", ".."):
        raise LedgerError(f"unsafe {label}: {value!r}")
    return value


@dataclass
class Entry:
    ts: float
    kind: str
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"ts": round(self.ts, 3), "kind": self.kind, **self.data}


class Ledger:
    """Append-only record for one pull request."""

    def __init__(self, path: Path, *, clock=time.time) -> None:
        self.path = Path(path)
        self._clock = clock

    @classmethod
    def for_pr(cls, state_dir: Path, *, host: str, owner: str, repo: str, number: int) -> "Ledger":
        _check_component(host, "host")
        _check_component(owner, "owner")
        _check_component(repo, "repo")
        if not isinstance(number, int) or number <= 0:
            raise LedgerError(f"bad PR number: {number!r}")
        return cls(Path(state_dir) / "prs" / host / owner / repo / f"{number}.jsonl")

    # -- writing -----------------------------------------------------------

    def append(self, kind: str, **data: Any) -> Entry:
        """Append one entry. Flushed and fsynced before returning."""
        if kind not in KINDS:
            raise LedgerError(f"unknown entry kind: {kind!r}")
        entry = Entry(ts=self._clock(), kind=kind, data=data)
        line = json.dumps(entry.as_dict(), sort_keys=True)
        if len(line) > MAX_LINE_BYTES:
            raise LedgerError(f"entry too large: {len(line)} bytes")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise LedgerError(f"cannot append to {self.path}: {exc.strerror}") from None
        return entry

    def record_write(self, *, op: str, outcome: str, **data: Any) -> Entry:
        """Record a write attempt and its state.

        ``uncertain`` is a first-class outcome: a write we cannot confirm is never assumed to have
        failed, because assuming that is how a duplicate comment gets posted on the next tick.
        """
        if outcome not in OUTCOMES:
            raise LedgerError(f"unknown write outcome: {outcome!r}")
        return self.append("write", op=op, outcome=outcome, **data)

    # -- reading -----------------------------------------------------------

    def entries(self) -> list[Entry]:
        """Every readable entry. A truncated final line is skipped, not fatal."""
        if not self.path.exists():
            return []
        out: list[Entry] = []
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise LedgerError(f"cannot read {self.path}: {exc.strerror}") from None
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                # A partial last line means the process died mid-write. Everything before it is
                # intact, and the partial line is exactly the entry that never landed.
                continue
            if isinstance(raw, dict) and "kind" in raw and "ts" in raw:
                kind = raw.pop("kind")
                raw.pop("ts", None)
                out.append(Entry(ts=0.0, kind=kind, data=raw))
        return out

    def unresolved_writes(self) -> list[dict[str, Any]]:
        """Writes left ``planned``, ``posting`` or ``uncertain`` — the ones a tick must settle."""
        open_states = {"planned", "posting", "uncertain"}
        pending: dict[str, dict[str, Any]] = {}
        for entry in self.entries():
            if entry.kind != "write":
                continue
            op_id = entry.data.get("op_id")
            if not op_id:
                continue
            state = entry.data.get("outcome")
            if state in open_states:
                pending[op_id] = entry.data
            elif state in ("confirmed", "refused"):
                pending.pop(op_id, None)
        return list(pending.values())

    def last_head(self) -> str | None:
        for entry in reversed(self.entries()):
            head = entry.data.get("head")
            if isinstance(head, str) and head:
                return head
        return None

    def budget_today(self, *, now: float | None = None) -> dict[str, Any]:
        """Spend recorded for the current UTC day. Unknown stays unknown, never zero."""
        now = self._clock() if now is None else now
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        total = 0.0
        unknown = 0
        for entry in self.entries():
            if entry.kind != "budget":
                continue
            if entry.data.get("day") != day:
                continue
            cost = entry.data.get("cost_usd")
            if cost is None:
                unknown += 1
            elif isinstance(cost, (int, float)):
                total += float(cost)
        return {"day": day, "spent_usd": round(total, 6), "unknown_cost_runs": unknown}

    # -- locking -----------------------------------------------------------

    @contextmanager
    def lock(self, *, timeout: float = 10.0, poll: float = 0.05) -> Iterator[None]:
        """Hold an exclusive lock for this PR.

        An OS advisory lock, not a pid file: the kernel releases it when the holder dies, so a
        killed process cannot leave the PR locked forever.
        """
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+")
        try:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    _acquire(handle)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise LedgerError(f"timed out waiting for the PR lock: {lock_path}") from None
                    time.sleep(poll)
            try:
                yield
            finally:
                _release(handle)
        finally:
            handle.close()


def _acquire(handle) -> None:
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release(handle) -> None:
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        import msvcrt

        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
