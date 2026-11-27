"""D16b: enabling agent approval takes a second, deliberate step.

`allow_agent_approval: true` was a single boolean. One line in one file turned on a mode where an agent posts
approvals on a human's behalf — and if the approval rules were loosened afterwards, the setting stayed on,
silently now applying to paths it was never confirmed for.

Enabling now needs an acknowledgement record:

* bound to the repository, so it cannot transfer to another repo;
* bound to a hash of the current `approvals.yml`, so editing the rules invalidates it;
* written only by an interactive confirmation that prints what will change and requires the repo name typed
  back. There is no `--yes` and no skip flag. With no terminal attached it refuses.

The record is a *second* gate, not a replacement: config validation still requires `identity_mode:
separated` and a VERIFIED container, because those are properties of the machine rather than of consent.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass


class ConfirmationRefused(Exception):
    """The confirmation did not happen. Nothing was written."""


def approvals_hash(approvals_yml: bytes | str) -> str:
    """A stable fingerprint of the rules the confirmation was given for.

    Whitespace-normalised so reformatting the file does not demand a fresh confirmation, while any change to
    a rule, a path or a count does. Normalising is deliberately conservative: it strips trailing spaces and
    collapses blank lines, nothing that could hide a semantic edit.
    """
    if isinstance(approvals_yml, str):
        raw = approvals_yml.encode("utf-8")
    else:
        raw = approvals_yml
    lines = [line.rstrip() for line in raw.decode("utf-8", "replace").splitlines()]
    normalised = "\n".join(line for line in lines if line.strip())
    return "sha256:" + hashlib.sha256(normalised.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Acknowledgement:
    """What was confirmed, and for exactly which rules."""

    repo: str
    approvals_sha: str
    rules_summary: str

    def to_json(self) -> str:
        return json.dumps(
            {"repo": self.repo, "approvals_sha": self.approvals_sha,
             "rules_summary": self.rules_summary},
            indent=2, sort_keys=True,
        )

    @classmethod
    def from_json(cls, text: str) -> "Acknowledgement":
        data = json.loads(text)
        return cls(
            repo=str(data["repo"]),
            approvals_sha=str(data["approvals_sha"]),
            rules_summary=str(data.get("rules_summary", "")),
        )


def ack_path(state_dir: str, repo: str) -> str:
    """One record per repository, on disk, so the acknowledgement is auditable rather than in memory."""
    safe = repo.replace("/", "__").replace("..", "")
    return os.path.join(state_dir, "agent-approval", f"{safe}.json")


def saved_ack(state_dir: str, repo: str) -> Acknowledgement | None:
    path = ack_path(state_dir, repo)
    try:
        with open(path) as handle:
            return Acknowledgement.from_json(handle.read())
    except (OSError, ValueError, KeyError):
        return None


def agent_approval_is_enabled(
    *, state_dir: str, repo: str, approvals_yml: bytes | str
) -> tuple[bool, str]:
    """Whether agent approval may run, and why not when it may not.

    Returns False when the acknowledgement is missing, belongs to another repository, or was given for a
    different version of the rules. Each case gets its own reason: "you never confirmed this" and "the rules
    changed since you confirmed" need different actions from the operator.
    """
    ack = saved_ack(state_dir, repo)
    if ack is None:
        return False, (
            f"agent approval is not enabled for {repo}. Run "
            f"`code-shipper config confirm-agent-approval --repo {repo}` and type the repository name back."
        )
    if ack.repo != repo:
        return False, f"the acknowledgement on disk is for {ack.repo}, not {repo}"
    current = approvals_hash(approvals_yml)
    if ack.approvals_sha != current:
        return False, (
            f"approvals.yml changed since agent approval was confirmed for {repo} "
            f"({ack.approvals_sha[:19]} → {current[:19]}). Loosening the rules needs a fresh "
            f"confirmation: `code-shipper config confirm-agent-approval --repo {repo}`."
        )
    return True, ""


def summarise_rules(approvals_yml: bytes | str) -> str:
    """A plain description of what the rules let the agent approve, for printing before the confirmation.

    A confirmation that does not say what it turns on is a rubber stamp.
    """
    if isinstance(approvals_yml, bytes):
        text = approvals_yml.decode("utf-8", "replace")
    else:
        text = approvals_yml
    lines = [line.rstrip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        return "the rules file is empty, so nothing would be approvable by the agent"
    return "\n".join(f"  {line}" for line in lines)


def confirm_interactively(
    *, repo: str, approvals_yml: bytes | str, state_dir: str, stdin=None, stdout=None
) -> Acknowledgement:
    """Ask, print what will change, require the repo name typed back, then write the record.

    Refuses with no terminal. There is no `--yes`: the entire value of the step is that a person read what
    they were enabling and said so by typing.
    """
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout

    if not getattr(stdin, "isatty", lambda: False)():
        raise ConfirmationRefused(
            "no terminal attached. This step exists so a person reads what agent approval enables and "
            "confirms it deliberately; it cannot be done non-interactively and has no --yes."
        )

    print(f"About to enable agent approval for {repo}.", file=stdout)
    print("", file=stdout)
    print("The agent will be able to post approvals on changes matching these rules:", file=stdout)
    print(summarise_rules(approvals_yml), file=stdout)
    print("", file=stdout)
    print("This is bound to the current approvals.yml. Editing that file invalidates it.", file=stdout)
    print(f"Type the repository name ({repo}) to confirm, or anything else to cancel:", file=stdout)
    typed = (stdin.readline() or "").strip()
    if typed != repo:
        raise ConfirmationRefused(
            f"cancelled: expected {repo!r}, got {typed!r}. Nothing was written."
        )

    ack = Acknowledgement(
        repo=repo,
        approvals_sha=approvals_hash(approvals_yml),
        rules_summary=summarise_rules(approvals_yml),
    )
    path = ack_path(state_dir, repo)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 0600: this is a consent record, not a secret, but it names a repository and its rules.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(ack.to_json())
    print(f"Confirmed. Record written to {path}", file=stdout)
    return ack
