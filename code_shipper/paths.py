"""Platform paths for state and configuration.

The ledger and locks live outside the repository and outside the user's home dotfiles where the
platform has a proper location for application state. Inside Hermes the plugin API supplies the
directory; standalone, it is the platform convention.

Nothing here creates or modifies a directory it was not asked for, and nothing here touches the
user's Hermes installation: the plugin path is supplied by the caller, not discovered by writing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "code-shipper"


class StateDirError(RuntimeError):
    """No safe place to keep state, so the caller must stop rather than guess."""


def default_state_dir(
    *, hermes_plugin_dir: Path | None = None, environ: dict[str, str] | None = None
) -> Path:
    """Where the ledger, locks and receipts live.

    Resolution order:
      1. ``CODE_SHIPPER_STATE_DIR`` — explicit override, used by tests and by operators
      2. the Hermes plugin storage directory, when the caller passes one
      3. the platform user-state directory

    The override is honoured first so a test or an operator can pin it, but it is never *created*
    here: the caller decides when to write to disk.
    """
    env = os.environ if environ is None else environ
    override = env.get("CODE_SHIPPER_STATE_DIR")
    if override:
        return Path(override)

    if hermes_plugin_dir is not None:
        return Path(hermes_plugin_dir)

    if sys.platform.startswith("win"):
        base = env.get("LOCALAPPDATA") or env.get("APPDATA")
        if not base:
            raise StateDirError("LOCALAPPDATA is not set, so there is no state directory")
        return Path(base) / APP_NAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME

    base = env.get("XDG_STATE_HOME")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".local" / "state" / APP_NAME


def ensure_dir(path: Path, *, mode: int = 0o700) -> Path:
    """Create a directory with an owner-only mode, and confirm it stayed that way.

    State contains PR numbers, heads and run history; another local user has no business reading it.
    The mode is set explicitly because mkdir's mode argument is masked by the umask.

    Windows: mode bits do not express directory ACLs there, so this cannot be enforced or verified.
    It is created and accepted, and the limitation is the same one that makes contained runs
    UNSUPPORTED on that platform (see contain/staging.py) rather than a silent pretend-success.
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        return path
    os.chmod(path, mode)
    actual = path.stat().st_mode & 0o777
    if actual != mode:
        raise StateDirError(f"{path} is {actual:04o}, expected {mode:04o}")
    return path
