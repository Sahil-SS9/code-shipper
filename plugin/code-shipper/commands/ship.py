"""The `/ship` command: a thin, honest wrapper over the CLI.

Two rules shape this file.

**The plugin contains no policy.** It does not decide what is safe; it calls the CLI, which holds the
broker, the secret scan and the authorisation gate. A second copy of a rule in a UI layer is how a rule
silently stops being enforced, so there is exactly one implementation and this is not it.

**Every write names what it will do before doing it.** `/ship ... publish` refuses unless
`allow_ship` is on, and even then prints the plan the CLI produced. A command that publishes on a
single word with no confirmation is a footgun with a friendly name.

Subcommands map to CLI verbs, so the two surfaces cannot drift: `review` → `review`, `fix` → `watch`,
`stop` → `stop`, `watch` → `poll`, `status` → `config effective`, `publish` → `ship`.
"""

from __future__ import annotations

import subprocess
import shlex
from typing import Any

# What each subcommand becomes. `publish` is separated because it is the only one that writes to a
# remote repository, and it is refused unless explicitly enabled.
SUBCOMMANDS: dict[str, str] = {
    "review": "review",
    "fix": "watch",
    "stop": "stop",
    "watch": "poll",
    "status": "config",
}

USAGE = (
    "usage: /ship <owner/repo> <pr> [review|fix|watch|stop|status|publish]\n"
    "\n"
    "  review   post a review of the current head (comment only) or status\n"
    "  fix      fix within a budget, in a container, and push to the PR branch\n"
    "  watch    poll for new heads and review each one\n"
    "  stop     stop a running watch loop\n"
    "  status   show the effective approvals policy for the repository\n"
    "  publish  open a pull request from a local branch (needs allow_ship)\n"
    "\n"
    "Code-Shipper never approves and never merges. A human merges."
)


class Refused(Exception):
    """A command that will not run, with a reason a user can act on."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def parse(argument: str) -> tuple[str, int, str]:
    """Split `/ship` arguments into (repo, pr, subcommand)."""
    parts = shlex.split(argument or "")
    if not parts:
        raise Refused(USAGE)
    if len(parts) < 2:
        raise Refused(f"need a repository and a pull request number.\n\n{USAGE}")
    repo = parts[0]
    if "/" not in repo:
        raise Refused(f"{repo!r} is not OWNER/NAME")
    try:
        number = int(parts[1].lstrip("#"))
    except ValueError:
        raise Refused(f"{parts[1]!r} is not a pull request number") from None
    subcommand = parts[2] if len(parts) > 2 else "review"
    known = set(SUBCOMMANDS) | {"publish"}
    if subcommand not in known:
        raise Refused(f"unknown subcommand {subcommand!r}; expected one of {sorted(known)}")
    return repo, number, subcommand


def build_argv(
    repo: str,
    number: int,
    subcommand: str,
    *,
    binary: str = "code-shipper",
    profile: str = "standard",
    extra: list[str] | None = None,
) -> list[str]:
    """The CLI invocation for a subcommand. Kept separate so it can be tested without running it."""
    if subcommand == "publish":
        # Publish needs more than a repo and a PR: it needs a branch, a base and an author. Refuse
        # here rather than guessing, because a guessed base branch is a PR against the wrong branch.
        raise Refused(
            "publish needs --branch, --base, --title and an author; run "
            "`code-shipper ship ...` from a terminal, which prints the plan and asks for "
            "confirmation."
        )
    verb = SUBCOMMANDS[subcommand]
    argv = [binary, verb]
    if verb == "poll":
        argv += [str(number), "--repo", repo]
    else:
        argv += ["--repo", repo]
        if verb in ("review", "watch"):
            argv += [str(number)]
    if verb == "review":
        argv += ["--profile", profile]
    if extra:
        argv += list(extra)
    return argv


def run(
    argument: str,
    *,
    binary: str = "code-shipper",
    profile: str = "standard",
    allow_ship: bool = False,
    runner: Any = None,
) -> dict:
    """Run the command and return its output. Never raises for a bad invocation; returns the reason."""
    try:
        repo, number, subcommand = parse(argument)
        if subcommand == "publish" and not allow_ship:
            raise Refused(
                "publishing is disabled for this plugin. Set allow_ship if you want a command to be "
                "able to open pull requests, and prefer `code-shipper ship` from a terminal, which "
                "shows the plan first."
            )
        argv = build_argv(repo, number, subcommand, binary=binary, profile=profile)
    except Refused as refused:
        return {"ok": False, "output": refused.message, "argv": []}

    execute = runner or _subprocess_runner
    try:
        completed = execute(argv)
    except FileNotFoundError:
        return {
            "ok": False,
            "output": f"{binary!r} was not found. Install it, or set the plugin's binary config.",
            "argv": argv,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "output": f"could not run {binary}: {exc}", "argv": argv}

    return {
        "ok": completed.returncode == 0,
        "output": (completed.stdout or "") + (completed.stderr or ""),
        "argv": argv,
        "returncode": completed.returncode,
    }


def _subprocess_runner(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=1800)


def handle(argument: str, config: dict | None = None) -> str:
    """Entry point for the host: returns text to show the user."""
    config = config or {}
    result = run(
        argument,
        binary=str(config.get("binary") or "code-shipper"),
        profile=str(config.get("default_profile") or "standard"),
        allow_ship=bool(config.get("allow_ship") or False),
    )
    return result["output"] or ("done" if result["ok"] else "failed with no output")
