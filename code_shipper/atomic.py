"""Atomic JSON writes, shared by every store that can be corrupted by a torn file.

The defect this exists to prevent: `Path.write_text` truncates the file before writing it, so a crash, a
full disk, a killed process or a concurrent reader sees a half-written file. `json.loads` then raises
`JSONDecodeError`, the loaders treat that as "no file yet", and the next save writes a fresh, *empty*
document over the old one. The data is gone and nothing logged a line.

That consequence is not equally serious everywhere, so the stores differ in what they do about it:

* `review/state.py` — losing it means the next review is a full one. Safe, and the write is best-effort.
* `review/tracked.py` — losing it wipes the tracked-findings ledger, including who asked and when.
* `review/memory.py` — losing it wipes stored lessons.
* `review/cache.py` — **may** stay a plain write. A cache miss costs one model call, which is exactly
  what a cache is for. Making it atomic would be work spent protecting data whose loss is the design.

One function, shared, because three copies of this is three chances for one of them to drift.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path, payload: Any, *, indent: int = 2,
                      sort_keys: bool = True) -> None:
    """Write `payload` as JSON so that a reader sees the old file or the new one, never a torn one.

    `os.replace` is atomic on POSIX and on Windows, so the rename is the only visible step.

    Raises `OSError` on failure — the caller decides whether a lost write matters. Swallowing it here
    would hide, from the stores where it *does* matter, that their data is gone.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    serialised = json.dumps(payload, indent=indent, sort_keys=sort_keys)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent),
        prefix=path.name + ".", suffix=".tmp", delete=False,
    )
    try:
        with handle:
            handle.write(serialised)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        # A temp file left behind would accumulate on every failure.
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON, returning `default` if the file is missing, unreadable or not valid JSON.

    A torn or corrupt file reads as `default`, exactly as before — this helper does not change that
    behaviour, it only stops three files from each implementing it slightly differently.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
