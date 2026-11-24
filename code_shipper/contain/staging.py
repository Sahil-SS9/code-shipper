"""Staging areas for contained runs.

A run needs three directories on the host: a read-only copy of the source, a writable workspace,
and a directory holding the broker socket. They must live somewhere ``ContainerSpec.validate``
will accept, which rules out any credential-bearing tree (``~/.hermes``, ``~/.ssh``, ``~/.config``).

The default is ``<repo>/.staging``, which is gitignored. It is deliberately *not* the Hermes
scratch cache: that lives under ``~/.hermes``, and allowing it would have meant carving an
exception into the mount guard — the guard is worth more than the convenience.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

STAGING_ENV = "CS_STAGING_ROOT"


def staging_root(repo_root: Path) -> Path:
    """Where runs are staged. ``CS_STAGING_ROOT`` overrides it; the path is validated on use."""
    configured = os.environ.get(STAGING_ENV)
    root = Path(configured) if configured else repo_root / ".staging"
    root.mkdir(parents=True, exist_ok=True)
    return root


def new_run_dir(repo_root: Path, prefix: str = "run-") -> Path:
    """A fresh per-run directory. The caller owns cleanup."""
    return Path(tempfile.mkdtemp(prefix=prefix, dir=staging_root(repo_root)))


def discard(path: Path, root: Path) -> None:
    """Remove a run directory, but only one that really is inside ``root``."""
    resolved = Path(path).resolve()
    if resolved == Path(root).resolve() or not resolved.is_relative_to(Path(root).resolve()):
        raise ValueError(f"refusing to remove {path}: not inside the staging root")
    shutil.rmtree(resolved, ignore_errors=True)
