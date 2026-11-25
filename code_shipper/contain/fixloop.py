"""Host-side checks and the leased push for a contained fix.

The worker's report of "what changed" is a claim by untrusted code. These functions re-derive the
truth from the files on disk, decide whether the change is allowed, and only then push — with an
expected-old-ref lease, fast-forward only, hooks disabled.

The scope rules exist because a contained agent can still write anywhere it has permission. Containment
stops it reaching credentials or the network; it does not stop it rewriting a test to make CI pass.
That check is this module's job.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..steward.policy import ScopePolicy, weakening

# There are no scope patterns here. Path rules and test-weakening detection live in exactly one place,
# `code_shipper.steward.policy`, and this module calls it. Two implementations of one rule is how one
# of them silently stops being enforced — the same reason `broker/write.py` became a re-export of
# `broker/roles.py` rather than a second copy.
MAX_FILES = 10
MAX_TOTAL_BYTES = 200_000


@dataclass
class ScopeVerdict:
    ok: bool
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def snapshot(root: Path) -> dict[str, str]:
    """sha256 per file, excluding .git. The worker's own report is not consulted.

    Keys are POSIX relative paths regardless of platform, matching what the rules and GitHub use.
    """
    digests: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            relative = path.relative_to(root).as_posix()
            digests[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return digests


def check_scope(
    before: dict[str, str],
    root: Path,
    *,
    original_root: Path | None = None,
    policy: ScopePolicy | None = None,
) -> ScopeVerdict:
    """Derive the change from disk and decide whether it is allowed.

    ``policy`` decides which paths may change and what counts as weakening a test. It is required for
    that judgement to mean anything: the defaults allow nothing, so a caller that omits it gets a
    refusal rather than a silent pass.

    ``original_root`` is the pristine copy the workspace was seeded from, used to compare test files
    against what they were. Without it a test edit cannot be judged at all.
    """
    policy = policy or ScopePolicy()
    after = snapshot(root)
    changed = sorted(path for path, digest in after.items() if before.get(path) != digest)
    removed = sorted(path for path in before if path not in after)
    problems: list[str] = []

    if not changed and not removed:
        return ScopeVerdict(False, changed, removed, ["no change was made"])
    if removed:
        problems.append(f"files were deleted: {removed}")
    if len(changed) > policy.max_files:
        problems.append(f"too many files changed: {len(changed)} > {policy.max_files}")

    total = 0
    for path in changed:
        if not policy.path_allowed(path):
            problems.append(f"out of scope: {path}")
            continue
        total += len((root / path).read_bytes())

    if total > policy.max_total_bytes:
        problems.append(f"change too large: {total} bytes > {policy.max_total_bytes}")

    # A change to a test must not be a way to make the test stop testing. Decided from the AST, so
    # class methods, unittest asserts and assert-True padding are all visible.
    if original_root is not None:
        for path in changed:
            if not policy.is_test_path(path):
                continue
            original = original_root / path
            before_text = original.read_text(encoding="utf-8", errors="replace") if original.exists() else ""
            after_text = (root / path).read_text(encoding="utf-8", errors="replace")
            problems.extend(f"{path}: {problem}" for problem in weakening(before_text, after_text))

    return ScopeVerdict(not problems, changed, removed, problems)


def test_weakening(
    before_root: Path, after_root: Path, *, policy: ScopePolicy | None = None
) -> list[str]:
    """Compare the tests between two trees for removed assertions or added skips."""
    policy = policy or ScopePolicy()
    problems: list[str] = []
    for path in sorted(after_root.rglob("*.py")):
        relative = path.relative_to(after_root).as_posix()
        if not policy.is_test_path(relative):
            continue
        original = before_root / relative
        before_text = original.read_text(encoding="utf-8", errors="replace") if original.exists() else ""
        problems.extend(
            f"{relative}: {p}"
            for p in weakening(before_text, path.read_text(encoding="utf-8", errors="replace"))
        )
    return problems


def _git(worktree: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run git in a worktree that may be owned by another uid.

    ``-c safe.directory=...`` on the command line is the only form git honours for the ownership
    check: it deliberately ignores repository-local config for that setting. This is narrower than
    a global safe.directory entry and needs no root.
    """
    return subprocess.run(
        ["git", "-c", f"safe.directory={worktree}", *args],
        cwd=worktree, capture_output=True, text=True, env=env,
    )


def push(
    worktree: Path,
    *,
    branch: str,
    expected_sha: str,
    commit_message: str,
    token: str,
    hookless_dir: Path,
    author: tuple[str, str],
    head_branch: str,
) -> dict:
    """Commit in the worktree and push fast-forward only, at the exact expected head.

    ``head_branch`` is the PR's head branch and is **required**: it is the only thing that stops a
    push landing somewhere else. When it had a default of None, "None means any branch" was the
    behaviour and the T-ADV-06 protection protected nothing — the one caller did not pass it. A
    required argument makes that mistake impossible to repeat rather than merely documented.

    Returns a receipt. Nothing here can merge: there is no merge operation in this module.
    """
    if not isinstance(head_branch, str) or not head_branch:
        return {
            "ok": False,
            "stage": "branch-scope",
            "detail": "head_branch is required: refusing to push without knowing the PR head",
        }
    if branch != head_branch:
        return {
            "ok": False,
            "stage": "branch-scope",
            "detail": f"refusing to push {branch!r}: only the PR head branch {head_branch!r}",
        }
    remote = _git(worktree, "ls-remote", "origin", f"refs/heads/{branch}")
    if remote.returncode != 0:
        return {"ok": False, "stage": "ls-remote", "detail": remote.stderr[-500:]}
    remote_sha = remote.stdout.split()[0] if remote.stdout.strip() else ""
    if remote_sha != expected_sha:
        return {
            "ok": False,
            "stage": "expected-head",
            "detail": f"remote {remote_sha[:12] or '(absent)'} != expected {expected_sha[:12]}",
        }

    env = {
        "PATH": "/usr/bin:/bin",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_ASKPASS": "/bin/false",
    }
    name, email = author
    commit = _git(
        worktree,
        "-c", f"user.name={name}", "-c", f"user.email={email}",
        "-c", f"core.hooksPath={hookless_dir}",
        "commit", "-a", "-m", commit_message,
        env=env,
    )
    if commit.returncode != 0:
        return {"ok": False, "stage": "commit", "detail": (commit.stderr or commit.stdout)[-500:]}

    new_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()

    local = _git(worktree, "cat-file", "-e", f"{expected_sha}^{{commit}}")
    if local.returncode != 0:
        return {"ok": False, "stage": "ancestry", "detail": "expected head not present locally"}
    ancestor = _git(worktree, "merge-base", "--is-ancestor", expected_sha, new_sha)
    if ancestor.returncode != 0:
        return {"ok": False, "stage": "fast-forward", "detail": "local head is not a descendant"}

    # The auth header must never be in argv: /proc/<pid>/cmdline is world-readable on a machine
    # without hidepid, so any local user could read the token mid-push. It goes through the
    # environment instead, which is owner-only, using git's GIT_CONFIG_* mechanism.
    basic = _basic_auth(token)
    auth_env = {
        **env,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
    }
    result = _git(
        worktree,
        "-c", f"core.hooksPath={hookless_dir}",
        "push",
        f"--force-with-lease=refs/heads/{branch}:{expected_sha}",
        "origin", f"{new_sha}:refs/heads/{branch}",
        env=auth_env,
    )
    return {
        "ok": result.returncode == 0,
        "stage": "push",
        "expected_sha": expected_sha,
        "new_sha": new_sha,
        "returncode": result.returncode,
        "detail": _redact((result.stderr or result.stdout)[-800:]),
    }


def _basic_auth(token: str) -> str:
    import base64

    return base64.b64encode(f"x-access-token:{token}".encode()).decode()


def _redact(text: str) -> str:
    text = re.sub(r"gh[pousr]_[A-Za-z0-9]{20,}", "***", text)
    text = re.sub(r"github_pat_[A-Za-z0-9_]{20,}", "***", text)
    return text
