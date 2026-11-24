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

# Paths a fix may touch. Anything else is out of scope and fails the push.
ALLOWED_PATTERNS = (
    re.compile(r"^calc/.*\.py$"),
    re.compile(r"^tests/.*\.py$"),
)
# The CI workflow, package metadata and policy files are never a fix.
FORBIDDEN_PATTERNS = (
    re.compile(r"^\.github/"),
    re.compile(r"(^|/)setup\.cfg$"),
    re.compile(r"(^|/)pyproject\.toml$"),
    # A conftest.py anywhere can rewrite collection, fixtures or assertions, so it is never a fix.
    # Matched on the basename at any depth, not just the repository root.
    re.compile(r"(^|/)conftest\.py$"),
    re.compile(r"(^|/)\.code-shipper/"),
)
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


def _posix(path: str) -> str:
    """Normalise a path to forward slashes before any rule is applied.

    The rules are written with '/' because GitHub reports POSIX paths. On Windows a relative path
    comes back with backslashes, so without this every rule silently stops matching: allowed paths
    look out of scope, and a forbidden file such as calc\\conftest.py slips through. Normalising here
    means there is one form for the rules to reason about.
    """
    return path.replace("\\", "/")


def _allowed(path: str) -> bool:
    normalised = _posix(path)
    # search(), not match(): the forbidden patterns are anchored on a path *component*, and match()
    # only anchors at the start of the string, so "calc/conftest.py" would slip through.
    if any(pattern.search(normalised) for pattern in FORBIDDEN_PATTERNS):
        return False
    return any(pattern.match(normalised) for pattern in ALLOWED_PATTERNS)


def _tests_only_tightened(before: bytes, after: bytes) -> list[str]:
    """A test change must not remove or skip assertions.

    This cannot judge intent; it can refuse the two shapes that make a failing test pass without
    fixing anything: deleting test functions, and skipping them.
    """
    problems: list[str] = []
    before_text = before.decode("utf-8", "replace")
    after_text = after.decode("utf-8", "replace")

    removed_defs = set(re.findall(r"^def (test_\w+)", before_text, re.M)) - set(
        re.findall(r"^def (test_\w+)", after_text, re.M)
    )
    if removed_defs:
        problems.append(f"test functions removed: {sorted(removed_defs)}")

    if re.search(r"@pytest\.mark\.(skip|xfail)", after_text) and not re.search(
        r"@pytest\.mark\.(skip|xfail)", before_text
    ):
        problems.append("a skip/xfail marker was added to a test")

    if after_text.count("assert ") < before_text.count("assert "):
        problems.append("assertions were removed from a test")

    return problems


def check_scope(
    before: dict[str, str], root: Path, *, original_root: Path | None = None
) -> ScopeVerdict:
    """Derive the change from disk and decide whether it is allowed.

    ``original_root`` is the pristine copy the workspace was seeded from, used to compare test files
    against what they were. Without it a test edit cannot be judged at all.
    """
    after = snapshot(root)
    changed = sorted(path for path, digest in after.items() if before.get(path) != digest)
    removed = sorted(path for path in before if path not in after)
    problems: list[str] = []

    if not changed and not removed:
        return ScopeVerdict(False, changed, removed, ["no change was made"])
    if removed:
        problems.append(f"files were deleted: {removed}")
    if len(changed) > MAX_FILES:
        problems.append(f"too many files changed: {len(changed)} > {MAX_FILES}")

    total = 0
    for path in changed:
        if not _allowed(path):
            problems.append(f"out of scope: {path}")
            continue
        total += len((root / path).read_bytes())

    if total > MAX_TOTAL_BYTES:
        problems.append(f"change too large: {total} bytes > {MAX_TOTAL_BYTES}")

    # A change to a test must not be a way to make the test stop testing.
    if original_root is not None:
        for path in changed:
            if not path.startswith("tests/"):
                continue
            original = original_root / path
            before_bytes = original.read_bytes() if original.exists() else b""
            problems.extend(
                f"{path}: {problem}"
                for problem in _tests_only_tightened(before_bytes, (root / path).read_bytes())
            )

    return ScopeVerdict(not problems, changed, removed, problems)


def test_weakening(before_root: Path, after_root: Path) -> list[str]:
    """Compare the tests directory between two trees for removed asserts or added skips."""
    problems: list[str] = []
    for path in sorted(after_root.glob("tests/**/*.py")):
        relative = str(path.relative_to(after_root))
        original = before_root / relative
        before = original.read_bytes() if original.exists() else b""
        problems.extend(f"{relative}: {p}" for p in _tests_only_tightened(before, path.read_bytes()))
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
) -> dict:
    """Commit in the worktree and push fast-forward only, at the exact expected head.

    Returns a receipt. Nothing here can merge: there is no merge operation in this module.
    """
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
