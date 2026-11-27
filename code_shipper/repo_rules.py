"""Repository rules files, read from the default branch only (N3, N4, N8).

Three files a repository can keep under `.code-shipper/`:

- `REVIEW.md`  — what "important" means here, what to always check, what to keep quiet (N3). Plus the
  conventions a repository already writes for agents: `AGENTS.md` and `CLAUDE.md` at the root.
- `STEWARD.md` — house rules for fixes: "regenerate the lockfile, don't hand-edit" (N4).
- `VERIFY.md`  — how to start the app and exercise a change (N8).

Why the default branch and never the pull request: these files steer the reviewer and the fixer. A pull
request that could edit them could tell the reviewer to ignore the very change it makes. Reading them at
the default branch means a change to the rules has been merged by a human first. N1 already stops a fix
from writing them; this stops a pull request from *being read* as them.

Why they still go only where lessons go: the rules are human-approved, which makes them safer than
learned lessons, but they are still text that can be worded to mean "stop reporting this". So the review
rules reach the same allowlisted lenses as lessons (never `security`, never `combined`) and never the
verifier, which decides what survives. The fix rules cannot loosen anything built in: they are given to
the fixing agent as guidance inside the same untrusted framing, and every limit (protected paths, scope,
budgets, three tries, never merge) is enforced in code after the agent has run, whatever the text said.

Reading is best-effort. A file that is absent is normal; a read that fails is reported, and the run goes on
without the file rather than failing a review over its own configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_RULES_CHARS = 6_000

REVIEW_FILES = (".code-shipper/REVIEW.md", "AGENTS.md", "CLAUDE.md")
STEWARD_FILES = (".code-shipper/STEWARD.md",)
VERIFY_FILES = (".code-shipper/VERIFY.md",)


@dataclass
class RepoRules:
    """The text of whichever rules files exist, and where each came from."""

    text: str = ""
    sources: list[str] = field(default_factory=list)
    ref: str = ""
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"sources": self.sources, "ref": self.ref, "chars": len(self.text), "errors": self.errors}


def load_rules(host: Any, repo: str, paths: tuple[str, ...], *, limit: int = MAX_RULES_CHARS) -> RepoRules:
    """Read `paths` at the repository's default branch. Never raises.

    `host` needs `default_branch(repo)` and `read_file(repo, ref, path)`; the pull request is not an
    argument, so there is no way for a caller to read these at the head by mistake.
    """
    rules = RepoRules()
    try:
        rules.ref = str(host.default_branch(repo) or "")
    except Exception as exc:  # noqa: BLE001 - configuration must never fail a run
        rules.errors.append(f"default branch unknown, rules files not read: {exc}")
        return rules
    if not rules.ref:
        rules.errors.append("default branch unknown, rules files not read")
        return rules
    sections: list[str] = []
    budget = limit
    for path in paths:
        try:
            raw = host.read_file(repo, rules.ref, path)
        except Exception as exc:  # noqa: BLE001
            rules.errors.append(f"{path}: could not be read ({exc})")
            continue
        if raw is None:
            continue
        text = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes) else str(raw).strip()
        if not text or budget <= 0:
            continue
        if len(text) > budget:
            text = text[:budget] + "\n[truncated]"
        budget -= len(text)
        sections.append(f"From {path}:\n{text}")
        rules.sources.append(path)
    rules.text = "\n\n".join(sections)
    return rules
