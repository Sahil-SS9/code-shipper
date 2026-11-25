"""Prepare a contained workspace for a fix (FR-S1, tech spec §6.1).

A fix runs inside a container with `--network none`, a read-only `/source`, a read-write `/work`, and
a broker socket it can reach but cannot impersonate. This module builds that workspace and nothing
else: it does not run the container (that is `fix.py`), and it does not decide scope (that is
`policy.py`).

Three properties matter and each is enforced here rather than assumed:

* **The workspace is a copy, never the checkout.** The worker edits `/work`; if it damaged the
  original there would be nothing to compare against.
* **The run directory is private.** `mkdtemp` gives 0700 and the broker socket sits inside it, which
  is what makes a 0666 socket acceptable — no other host user can traverse the directory to reach it.
  Re-checked immediately before use, because a check that happened at creation proves nothing later.
* **Stale state is cleared.** A workspace carrying a previous run's edits would make a diff include
  work nobody asked for, and the scope check would then be judging the wrong change.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Copied into /work so a worker can run the suite without network access.
COPY_IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".staging", "*.egg-info")


class WorkspaceError(Exception):
    """The workspace could not be prepared safely. Never continue past this."""


@dataclass
class Workspace:
    """A prepared run directory: the editable copy, the pristine reference, and the broker socket."""

    root: Path
    work: Path
    pristine: Path
    broker_dir: Path
    base_sha: str = ""
    head_sha: str = ""
    branch: str = ""
    files: list[str] = field(default_factory=list)
    detail: dict[str, object] = field(default_factory=dict)
    keep: bool = False

    @property
    def socket_path(self) -> Path:
        return self.broker_dir / "broker.sock"

    @property
    def checkout(self) -> Path:
        """The tree the worker edits and the scope check judges.

        `/work` is scratch (task.json, capability, the harness's own home); `/work/repo` is the
        checkout. The distinction matters: judging `/work` would include the worker's own scratch
        files, and pushing from it would commit them.
        """
        return self.work / "repo"

    def describe(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "work": str(self.work),
            "pristine": str(self.pristine),
            "branch": self.branch,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "file_count": len(self.files),
        }

    def cleanup(self) -> None:
        """Remove the run directory. Silently ignores a workspace that is already gone."""
        shutil.rmtree(self.root, ignore_errors=True)


def _git(cwd: Path, *args: str) -> str:
    """Run git with a per-invocation safe.directory.

    `chown` is unavailable without root, and a repository-local safe.directory is ignored by git by
    design, so the ownership check has to be passed per invocation.
    """
    result = subprocess.run(
        ["git", "-c", f"safe.directory={cwd}", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)} failed: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


def require_private_dir(path: Path) -> None:
    """Refuse a run directory that another host user could reach.

    The broker socket is 0666 so the worker (a different uid inside the container) can connect. That
    is only safe because this directory is 0700 and owned by us. A world- or group-writable parent
    would let another user replace the socket, which is the whole boundary.
    """
    try:
        info = path.stat()
    except OSError as exc:
        raise WorkspaceError(f"run directory is not usable: {exc}") from None
    if not stat.S_ISDIR(info.st_mode):
        raise WorkspaceError(f"{path} is not a directory")
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o077:
        raise WorkspaceError(
            f"run directory {path} is {oct(mode)}; it must be 0700 because the broker socket inside "
            "it is reachable by the worker"
        )
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise WorkspaceError(
            f"run directory {path} is owned by uid {info.st_uid}, not {os.geteuid()}"
        )


def prepare(
    repo_dir: Path,
    *,
    base_sha: str,
    head_sha: str,
    branch: str,
    staging_root: Path | None = None,
    run_id: str = "",
    reference_dir: Path | None = None,
    remote: str = "",
    keep: bool = False,
) -> Workspace:
    """Build the editable copy plus a pristine reference of the same tree.

    ``reference_dir`` is the checkout the head was taken from. When omitted, ``repo_dir`` is used. The
    pristine copy is what test-weakening is judged against, so it must be the **head**, not the base:
    the comparison asked is "did this fix weaken the tests as they arrived", not "did it change them".
    """
    staging_root = staging_root or (repo_dir / ".staging")
    staging_root.mkdir(parents=True, exist_ok=True)

    root = Path(_mkdtemp(staging_root, run_id))
    try:
        require_private_dir(root)

        source = reference_dir or repo_dir
        work = root / "work"
        pristine = root / "pristine"
        broker_dir = root / "broker"
        for target in (work, pristine, broker_dir):
            target.mkdir()

        # Inside the container /work is scratch and the checkout lives at /work/repo, which is the
        # layout fix_worker.py reads. Build the same shape here so the host sees what the worker does.
        work_repo = work / "repo"
        work_repo.mkdir()

        # The pristine copy is taken first, from the head tree, so it can never include the fix.
        # The pristine copy is only ever read, so it needs neither a remote nor the real commit.
        _materialise(source, pristine, head_sha)
        _materialise(source, work_repo, head_sha, remote=remote)

        # The worker runs as uid 65534 in the container, so it must be able to write /work.
        _make_readable(work)

        # The broker directory holds the socket; the worker needs to reach it but not to write to it.
        _make_relayable(broker_dir)
        require_private_dir(root)

        files = sorted(
            str(path.relative_to(work_repo)).replace(os.sep, "/")
            for path in work_repo.rglob("*")
            if path.is_file()
        )
        return Workspace(
            root=root,
            work=work,
            pristine=pristine,
            broker_dir=broker_dir,
            base_sha=base_sha,
            head_sha=head_sha,
            branch=branch,
            files=files,
            detail={"staging_root": str(staging_root)},
            keep=keep,
        )
    except Exception:
        if not keep:
            shutil.rmtree(root, ignore_errors=True)
        raise


def _mkdtemp(parent: Path, run_id: str) -> str:
    prefix = f"run-{run_id}-" if run_id else "run-"
    import tempfile

    return tempfile.mkdtemp(prefix=prefix, dir=str(parent))


def _materialise(source: Path, target: Path, sha: str, *, remote: str = "") -> None:
    """Copy the checkout at ``sha`` into ``target``.

    A worktree would be cheaper, but it writes to the source repository's `.git` and the worker's copy
    must be disposable. Copying the working tree and then checking out the exact commit keeps the
    source repository untouched.

    ``remote`` is set as ``origin`` when given, because the push stage authenticates against a real
    remote and a checkout with no origin cannot be pushed at all.
    """
    for entry in source.iterdir():
        if entry.name in (".git", ".staging") or entry.name.startswith(".staging"):
            continue
        destination = target / entry.name
        if entry.is_dir():
            shutil.copytree(entry, destination, ignore=COPY_IGNORE, symlinks=True, dirs_exist_ok=True)
        else:
            shutil.copy2(entry, destination)

    # A `.git` is needed so the copy can be committed from; it is initialised fresh and never shares
    # objects with the source checkout. It is also set to the **exact head commit**, because the push
    # stage is fast-forward only: a local commit whose parent is not the remote head cannot be pushed,
    # and inventing a base commit would make every push a non-fast-forward.
    subprocess.run(["git", "init", "-q"], cwd=str(target), check=True, capture_output=True)
    if remote:
        subprocess.run(
            ["git", "remote", "add", "origin", remote],
            cwd=str(target), check=True, capture_output=True,
        )
        if sha and len(sha) == 40:
            subprocess.run(
                ["git", "fetch", "-q", "--depth", "1", "origin", sha],
                cwd=str(target), check=True, capture_output=True,
            )
            # --mixed, not --soft: the index must be rebuilt from the *fetched tree*, not carried over
            # from the empty fresh repository. With --soft the index still listed no files, so the
            # push committed every file as deleted. Caught on sandbox PR #23, where a one-file fix
            # arrived as a diff that removed the whole repository.
            subprocess.run(
                ["git", "reset", "-q", "--mixed", "FETCH_HEAD"],
                cwd=str(target), check=True, capture_output=True,
            )
            # The working tree already holds the head content (copied above); assert that, because a
            # mismatch here is what silently turns a fix into a deletion patch.
            missing = [
                entry.name for entry in source.iterdir()
                if entry.name not in (".git", ".staging")
                and not entry.name.startswith(".staging")
                and not (target / entry.name).exists()
            ]
            if missing:
                raise WorkspaceError(
                    f"workspace is missing {missing} after checkout; refusing to continue"
                )
            return
    subprocess.run(["git", "add", "-A"], cwd=str(target), check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Code-Shipper", "-c", "user.email=bot@invalid",
         "commit", "-qm", f"workspace base {sha[:12]}"],
        cwd=str(target), check=True, capture_output=True,
    )


def _make_readable(work: Path) -> None:
    """Let the container uid write the copy.

    `chown` needs root, which is unavailable, so permissions are widened on the workspace instead. The
    directory is 0700 and holds nothing but a disposable copy, so this does not widen the boundary.
    """
    for path in [work, *work.rglob("*")]:
        try:
            if path.is_dir():
                path.chmod(0o777)
            elif not path.is_symlink():
                path.chmod(0o666)
        except OSError:
            continue


def _make_relayable(broker_dir: Path) -> None:
    """The worker must traverse and connect, but not create or replace."""
    try:
        broker_dir.chmod(0o755)
    except OSError:
        pass
