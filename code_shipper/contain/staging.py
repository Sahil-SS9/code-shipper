"""Staging areas for contained runs.

A run needs three directories on the host: a read-only copy of the source, a writable workspace,
and a directory holding the broker socket. They must live somewhere ``ContainerSpec.validate``
will accept, which rules out any credential-bearing tree (``~/.hermes``, ``~/.ssh``, ``~/.config``).

The default is ``<repo>/.staging``, which is gitignored. It is deliberately *not* the Hermes
scratch cache: that lives under ``~/.hermes``, and allowing it would have meant carving an
exception into the mount guard — the guard is worth more than the convenience.

Permission enforcement
----------------------
The broker socket is mode 0o666 so a worker running as uid 65534 in the container's user namespace
can connect to a broker running as uid 1001 on the host. A shared group would be a system change and
was ruled out, so the socket mode cannot be the control.

**The real control is the run directory itself.** ``mkdtemp`` creates it 0o700, owned by the broker's
user. Another host user cannot traverse it, so it cannot reach the socket inside it, whatever the
socket's own mode says. That property is worth nothing if it is accidental, so ``new_run_dir``
verifies it and ``require_private_run_dir`` re-verifies it immediately before the broker starts.

``CS_STAGING_ROOT`` cannot weaken this: only the *parent* is configurable, and the guard applies to
the run directory that is created inside it. A world-writable parent is rejected outright rather
than trusted.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path

STAGING_ENV = "CS_STAGING_ROOT"

# The run directory must be exactly 0700: no group, no other, and setuid/setgid/sticky all clear.
REQUIRED_MODE = 0o700


class StagingPermissionError(RuntimeError):
    """The run directory is not host-private, so the socket control does not hold."""


def staging_root(repo_root: Path) -> Path:
    """Where runs are staged. ``CS_STAGING_ROOT`` overrides the parent; it is validated, not trusted."""
    configured = os.environ.get(STAGING_ENV)
    root = Path(configured) if configured else repo_root / ".staging"
    root.mkdir(parents=True, exist_ok=True)
    _require_private_parent(root)
    return root


def _require_private_parent(root: Path) -> None:
    """The parent may be group/world *readable*, but must not be group/world writable.

    A writable parent would let another user plant or rename a run directory. Requiring 0700 on the
    parent too would be stricter than needed and would break the common case of a shared checkout,
    so the rule is the narrow one: nobody else may write here.
    """
    info = os.stat(root)
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise StagingPermissionError(
            f"staging root {root} is group/world writable "
            f"({stat.S_IMODE(info.st_mode):04o}); another user could plant a run directory"
        )


def new_run_dir(repo_root: Path, prefix: str = "run-") -> Path:
    """A fresh per-run directory, verified 0700 and owned by this user."""
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=staging_root(repo_root)))
    # mkdtemp already uses 0700; assert it rather than assume, because this is the control that
    # makes the 0o666 broker socket acceptable.
    os.chmod(path, REQUIRED_MODE)
    require_private_run_dir(path)
    return path


def require_private_run_dir(path: Path) -> None:
    """Refuse to proceed unless ``path`` is 0700 and owned by the current user.

    Called twice on purpose: once when the directory is made, and again by the harness immediately
    before the broker binds a socket inside it. The second call catches a widening that happened in
    between, which is exactly the failure this control exists to survive.

    Windows: directory mode bits do not express access control there (``os.chmod`` only toggles the
    read-only flag, and a directory reports 0777 regardless of its ACL), so this control cannot be
    evaluated. That is a capability gap, not a pass — refuse with a message that says so rather than
    reporting a misleading mode. Contained fixing on Windows is UNSUPPORTED for the same reason.
    """
    if os.name == "nt":
        raise StagingPermissionError(
            "cannot verify that the run directory is host-private on Windows: directory mode bits "
            "do not express ACLs there, so the control that makes the broker socket acceptable "
            "cannot be evaluated. Contained runs are UNSUPPORTED on this platform."
        )

    try:
        info = os.stat(path)
    except OSError as exc:
        raise StagingPermissionError(f"run directory unavailable: {path}: {exc.strerror}") from None

    if not stat.S_ISDIR(info.st_mode):
        raise StagingPermissionError(f"run path is not a directory: {path}")

    mode = stat.S_IMODE(info.st_mode)
    if mode != REQUIRED_MODE:
        raise StagingPermissionError(
            f"run directory {path} is {mode:04o}, must be {REQUIRED_MODE:04o}: "
            "another host user could reach the broker socket and the run capability"
        )

    if info.st_uid != os.getuid():
        raise StagingPermissionError(
            f"run directory {path} is owned by uid {info.st_uid}, not this user ({os.getuid()})"
        )


def discard(path: Path, root: Path) -> None:
    """Remove a run directory, but only one that really is inside ``root``."""
    resolved = Path(path).resolve()
    if resolved == Path(root).resolve() or not resolved.is_relative_to(Path(root).resolve()):
        raise ValueError(f"refusing to remove {path}: not inside the staging root")
    shutil.rmtree(resolved, ignore_errors=True)
