"""The code-host interface. Reads only.

Writes are deliberately absent. Everything that changes a repository goes through the broker, whose
allowlist has no merge operation, so a bug in an adapter cannot approve or merge. Keeping reads and
writes in separate interfaces is what makes that a structural property rather than a convention.

Every field here is treated as untrusted data when it reaches a prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    author: str
    head_sha: str
    head_ref: str
    base_ref: str
    # The commit the base branch pointed at when the PR was read. The approvals policy is fetched at
    # this SHA rather than at the branch name, because a branch moves and a verdict must describe a
    # fixed tree.
    base_sha: str = ""
    draft: bool = False
    from_fork: bool = False
    labels: tuple[str, ...] = ()
    state: str = "open"
    # The author's own description. Untrusted: it is written by the person who made the change.
    body: str = ""


@dataclass(frozen=True)
class FilePatch:
    path: str
    status: str = "modified"          # added | modified | removed | renamed
    old_path: str | None = None
    patch: str = ""

    @property
    def display_path(self) -> str:
        if self.old_path and self.old_path != self.path:
            return f"{self.old_path} -> {self.path}"
        return self.path


@dataclass(frozen=True)
class Diff:
    files: tuple[FilePatch, ...] = ()

    @property
    def additions(self) -> int:
        return sum(
            1 for f in self.files for line in f.patch.splitlines() if line.startswith("+")
            and not line.startswith("+++")
        )

    @property
    def deletions(self) -> int:
        return sum(
            1 for f in self.files for line in f.patch.splitlines() if line.startswith("-")
            and not line.startswith("---")
        )


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str                       # queued | in_progress | completed
    conclusion: str | None = None     # success | failure | neutral | cancelled | skipped | timed_out
    details_url: str | None = None
    check_id: int | None = None

    @property
    def failed(self) -> bool:
        return self.status == "completed" and self.conclusion in {
            "failure", "timed_out", "cancelled"
        }


@dataclass(frozen=True)
class Comment:
    id: int
    author: str
    body: str
    created_at: str = ""
    path: str | None = None
    line: int | None = None


@dataclass(frozen=True)
class Review:
    id: int
    author: str
    state: str                        # APPROVED | CHANGES_REQUESTED | COMMENTED | DISMISSED
    body: str = ""


@dataclass(frozen=True)
class RepoInfo:
    owner: str
    name: str
    default_branch: str
    private: bool = True

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"


class CodeHost(Protocol):
    """Read operations a code host must provide. No writes; see the module docstring."""

    name: str

    def get_pr(self, repo: str, number: int) -> PullRequest: ...

    def list_open_prs(self, repo: str, updated_since: str | None = None) -> list[PullRequest]: ...

    def get_diff(self, repo: str, number: int) -> Diff: ...

    def get_checks(self, repo: str, sha: str) -> list[CheckResult]: ...

    def get_check_log(self, repo: str, check: CheckResult) -> str: ...

    def list_comments(self, repo: str, number: int, since: str | None = None) -> list[Comment]: ...

    def list_reviews(self, repo: str, number: int) -> list[Review]: ...

    def permission_of(self, repo: str, login: str) -> str: ...

    def read_file(self, repo: str, ref: str, path: str) -> bytes | None: ...

    def default_branch(self, repo: str) -> str: ...


class HostError(Exception):
    """A read failed. ``status`` is the HTTP status when there was one."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def split_slug(slug: str) -> tuple[str, str]:
    """Validate an owner/name slug. Refuses anything that could be path-concatenated."""
    if not isinstance(slug, str) or slug.count("/") != 1:
        raise HostError(f"invalid repository slug: {slug!r}")
    owner, name = slug.split("/")
    for part in (owner, name):
        if not part or part in (".", ".."):
            raise HostError(f"invalid repository slug: {slug!r}")
        if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._" for c in part):
            raise HostError(f"invalid repository slug: {slug!r}")
    return owner, name
