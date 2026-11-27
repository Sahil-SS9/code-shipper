"""N5: review before you push. `code-shipper review --local`.

Reviews the current branch plus uncommitted and untracked changes against its base, with no pull request.
Nothing is posted, nothing is stored, no GitHub token is read. The exit code follows the same contract as a
pull-request review, so it can sit in a pre-push hook.

The base is the merge base with the default branch, found locally (`origin/HEAD`, then `main`, then
`master`), or `--base REF`. The repository's review rules (N3) are read at that base ref with `git show`,
never from the working tree, for the same reason a pull request's own rules files are never read: the
change under review must not be able to rewrite the rules it is reviewed by.

Not built: `--apply` (writing fixes into the working tree). A fix needs the contained worker and its scope
checks; doing that on a developer's checkout is a different trust boundary and is left for a later round.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..hosts.base import Diff, FilePatch

MAX_UNTRACKED_BYTES = 200_000


class LocalReviewError(Exception):
    pass


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          errors="replace", timeout=60)
    if check and proc.returncode != 0:
        raise LocalReviewError(f"git {' '.join(args)}: {proc.stderr.strip() or proc.returncode}")
    return proc.stdout


@dataclass
class LocalChange:
    diff: Diff
    base_ref: str
    merge_base: str
    untracked: int


def find_base(repo: Path, base: str = "") -> str:
    if base:
        _git(repo, "rev-parse", "--verify", "--quiet", base + "^{commit}")
        return base
    head = _git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", check=False).strip()
    for candidate in (head, "origin/main", "main", "origin/master", "master"):
        if candidate and _git(repo, "rev-parse", "--verify", "--quiet", candidate + "^{commit}",
                              check=False).strip():
            return candidate
    raise LocalReviewError("no base found: pass --base REF (looked for origin/HEAD, main and master)")


def parse_unified(text: str) -> tuple[FilePatch, ...]:
    """`git diff` output into the same FilePatch shape the GitHub API gives: hunks only, from `@@`."""
    files: list[FilePatch] = []
    path = old_path = ""
    status = "modified"
    hunks: list[str] = []
    in_hunks = False

    def flush() -> None:
        if path:
            files.append(FilePatch(path=path, status=status,
                                   old_path=old_path if status == "renamed" else None,
                                   patch="\n".join(hunks)))

    for line in text.splitlines():
        if line.startswith("diff --git "):
            flush()
            parts = line[len("diff --git "):].split(" b/", 1)
            old_path = parts[0][2:] if parts[0].startswith("a/") else parts[0]
            path = parts[1] if len(parts) == 2 else old_path
            status, hunks, in_hunks = "modified", [], False
        elif not in_hunks and line.startswith("new file mode"):
            status = "added"
        elif not in_hunks and line.startswith("deleted file mode"):
            status = "removed"
        elif not in_hunks and line.startswith("rename from "):
            status, old_path = "renamed", line[len("rename from "):]
        elif not in_hunks and line.startswith("rename to "):
            path = line[len("rename to "):]
        elif line.startswith("@@"):
            in_hunks = True
            hunks.append(line)
        elif in_hunks:
            hunks.append(line)
    flush()
    return tuple(files)


def _untracked(repo: Path) -> list[FilePatch]:
    out: list[FilePatch] = []
    for name in _git(repo, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        if not name:
            continue
        file = repo / name
        try:
            data = file.read_bytes()
        except OSError:
            continue
        if len(data) > MAX_UNTRACKED_BYTES or b"\0" in data[:8000]:
            continue
        lines = data.decode("utf-8", errors="replace").splitlines()
        patch = f"@@ -0,0 +1,{len(lines)} @@\n" + "\n".join("+" + line for line in lines)
        out.append(FilePatch(path=name, status="added", patch=patch))
    return out


def local_change(repo: Path, base: str = "") -> LocalChange:
    repo = Path(repo)
    if not _git(repo, "rev-parse", "--is-inside-work-tree", check=False).strip() == "true":
        raise LocalReviewError(f"{repo} is not a git working tree")
    base_ref = find_base(repo, base)
    merge_base = _git(repo, "merge-base", base_ref, "HEAD").strip()
    # Working tree (staged and unstaged) against the merge base: everything this branch would push.
    tracked = parse_unified(_git(repo, "diff", "--no-color", "--no-ext-diff", "-M", "-U3", merge_base))
    untracked = _untracked(repo)
    return LocalChange(diff=Diff(files=tracked + tuple(untracked)), base_ref=base_ref,
                       merge_base=merge_base, untracked=len(untracked))


class LocalRulesHost:
    """The two calls `repo_rules.load_rules` makes, answered from git at the base ref."""

    def __init__(self, repo: Path, base_ref: str) -> None:
        self.repo, self.base_ref = Path(repo), base_ref

    def default_branch(self, repo: str) -> str:
        return self.base_ref

    def read_file(self, repo: str, ref: str, path: str) -> bytes | None:
        proc = subprocess.run(["git", "-C", str(self.repo), "show", f"{ref}:{path}"],
                              capture_output=True, timeout=30)
        return proc.stdout if proc.returncode == 0 else None
