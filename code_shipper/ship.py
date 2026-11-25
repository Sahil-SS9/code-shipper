"""`/ship`: take a local change to an open pull request (Phase 5).

The order is deliberate and enforced, not advisory:

    plan → secret scan → explicit authorisation → branch → commit → push → PR → review

Each step can refuse, and the refusals that matter are:

**A secret blocks before anything is written.** The scan runs on the exact content that would be
committed, not on a summary of it.

**Publishing requires a recorded authorisation.** Not an interactive prompt — a recorded one. A non-TTY
run with no authorisation must fail with an explanation rather than hang waiting for a human who is not
there. That is a gate requirement, and it is the difference between a tool that can run unattended and
one that publishes whatever it is pointed at.

**One commit, authored as the configured identity.** Every commit carries the configured author so work
counts on the right contribution graph, and the message records that the change came through
Code-Shipper.

Nothing here merges, and nothing here can: the broker has no merge operation.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import secrets as secrets_mod

# The steps, in order. A caller may stop before any of them, never reorder them.
STEPS = ("plan", "scan", "authorise", "branch", "commit", "push", "pr")


class ShipRefused(Exception):
    """A ship step refused. ``code`` is stable; ``detail`` is for a human."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass
class Authorisation:
    """A recorded decision to publish. Without one, nothing is pushed.

    Recorded rather than prompted: a non-TTY run must fail with a message instead of hanging, and a
    prompt that is answered by default is not authorisation.
    """

    granted: bool = False
    by: str = ""
    reason: str = ""

    @classmethod
    def from_flag(cls, value: str | None, *, by: str = "") -> "Authorisation":
        if value is None:
            return cls(granted=False, reason="no authorisation recorded")
        text = str(value).strip().lower()
        if text not in ("yes", "true", "1", "i-authorise-publish"):
            return cls(granted=False, reason=f"authorisation value {value!r} is not affirmative")
        return cls(granted=True, by=by or "cli", reason="authorised on the command line")

    def require(self) -> None:
        if not self.granted:
            raise ShipRefused(
                "not_authorised",
                "publishing needs a recorded authorisation; pass --i-authorise-publish in a script, "
                "or confirm interactively when a terminal is attached. Refusing rather than "
                "publishing unprompted.",
            )


@dataclass
class Plan:
    repo: str
    branch: str
    base: str
    title: str
    body: str
    files: dict[str, str] = field(default_factory=dict)
    author_name: str = ""
    author_email: str = ""
    draft: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "branch": self.branch,
            "base": self.base,
            "title": self.title,
            "body": self.body,
            "files": sorted(self.files),
            "author": f"{self.author_name} <{self.author_email}>",
            "draft": self.draft,
            "steps": list(STEPS),
        }

    def render(self) -> str:
        """What the user confirms. Names every file and the exact identity used."""
        lines = [
            f"Ship plan for {self.repo}",
            "",
            f"  branch:  {self.branch}",
            f"  base:    {self.base}",
            f"  title:   {self.title}",
            f"  author:  {self.author_name} <{self.author_email}>",
            f"  draft:   {self.draft}",
            "",
            f"  {len(self.files)} file(s):",
        ]
        for path in sorted(self.files):
            lines.append(f"    {path}")
        lines += [
            "",
            "Steps: " + " → ".join(STEPS),
            "",
            "This will create a branch, a commit and a pull request. It will not merge anything, and "
            "Code-Shipper never approves its own pull request.",
        ]
        return "\n".join(lines)


def make_plan(
    *,
    repo: str,
    branch: str,
    base: str,
    title: str,
    body: str = "",
    files: dict[str, str],
    author_name: str,
    author_email: str,
    draft: bool = False,
) -> Plan:
    """Validate and assemble a plan. Refuses rather than filling in a default."""
    if not repo or "/" not in repo:
        raise ShipRefused("bad_repo", f"{repo!r} is not OWNER/NAME")
    if not branch.strip():
        raise ShipRefused("empty_branch", "a branch name is required")
    if not base.strip():
        raise ShipRefused("empty_base", "a base branch is required; there is no default")
    if branch == base:
        raise ShipRefused("branch_is_base", f"branch and base are both {branch!r}")
    if not title.strip():
        raise ShipRefused("empty_title", "a pull request title is required")
    if not author_name.strip() or not author_email.strip():
        raise ShipRefused(
            "no_author", "an author name and email are required so the commit counts as yours"
        )
    if not files:
        raise ShipRefused("empty_change", "nothing to ship")
    return Plan(
        repo=repo, branch=branch, base=base, title=title, body=body,
        files=dict(files), author_name=author_name, author_email=author_email, draft=draft,
    )


def check_secrets(plan: Plan) -> None:
    """Scan the exact content that would be committed, and refuse if anything is found."""
    findings = secrets_mod.scan_files(plan.files)
    if findings:
        raise secrets_mod.SecretFound(findings)


@dataclass
class ShipResult:
    branch: str = ""
    commit: str = ""
    pushed: bool = False
    pr: int = 0
    pr_url: str = ""
    steps_done: list[str] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch,
            "commit": self.commit,
            "pushed": self.pushed,
            "pr": self.pr,
            "pr_url": self.pr_url,
            "steps_done": list(self.steps_done),
            "findings": list(self.findings),
        }


def _git(args: list[str], *, cwd: Path, author: tuple[str, str]) -> str:
    """Run git with the configured identity on every command, not just the commit."""
    name, email = author
    command = ["git", "-c", f"user.name={name}", "-c", f"user.email={email}"] + args
    result = subprocess.run(
        command, cwd=str(cwd), capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise ShipRefused(
            "git_failed", f"git {' '.join(args[:2])} failed: {(result.stderr or '').strip()[:300]}"
        )
    return result.stdout


def ship(
    plan: Plan,
    *,
    workdir: Path,
    authorisation: Authorisation,
    broker: Any = None,
    dry_run: bool = False,
    log: Callable[[str], None] | None = None,
) -> ShipResult:
    """Take the plan to an open PR. Refuses at the first step that cannot proceed."""
    say = log or (lambda _message: None)
    result = ShipResult(branch=plan.branch)
    author = (plan.author_name, plan.author_email)

    say("plan: " + ", ".join(sorted(plan.files)))
    result.steps_done.append("plan")

    # 1. Secrets, before anything is written to disk or to git.
    try:
        check_secrets(plan)
    except secrets_mod.SecretFound as found:
        result.findings = [finding.as_dict() for finding in found.findings]
        say(secrets_mod.report(found.findings))
        raise
    result.steps_done.append("scan")
    say("scan: no secrets found")

    # 2. Authorisation, before a branch exists.
    authorisation.require()
    result.steps_done.append("authorise")

    if dry_run:
        say("dry run: stopping before any write")
        return result

    workdir = Path(workdir)
    # 3. Branch.
    _git(["checkout", "-B", plan.branch], cwd=workdir, author=author)
    result.steps_done.append("branch")

    # 4. Files, then one commit.
    for path, content in plan.files.items():
        target = workdir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(["add", "--", *sorted(plan.files)], cwd=workdir, author=author)
    message = f"{plan.title}\n\nShipped with Code-Shipper. Nothing here approves or merges.\n"
    _git(["commit", "-m", message], cwd=workdir, author=author)
    result.commit = _git(["rev-parse", "HEAD"], cwd=workdir, author=author).strip()
    result.steps_done.append("commit")
    say(f"commit: {result.commit[:12]}")

    # 5. Push, this branch only.
    _git(["push", "-u", "origin", f"HEAD:refs/heads/{plan.branch}"], cwd=workdir, author=author)
    result.pushed = True
    result.steps_done.append("push")
    say(f"push: {plan.branch}")

    # 6. PR, through the broker so it is ledgered and refusable.
    if broker is None:
        say("no broker: leaving the branch pushed and no PR opened")
        return result
    created = broker.create_pr(
        title=plan.title, head=plan.branch, base=plan.base, body=plan.body, draft=plan.draft
    )
    result.pr = int(created.get("number") or 0)
    result.pr_url = str(created.get("html_url") or "")
    result.steps_done.append("pr")
    say(f"pr: #{result.pr}")
    return result
