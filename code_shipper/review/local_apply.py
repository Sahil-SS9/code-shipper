"""`review --local --apply`: fix Important findings in your own checkout, safely.

The trust boundary is the developer's working tree, so the rules are stricter than on a pull request:

* **The agent never touches your checkout.** It runs in the same contained worker a PR fix uses, on a
  copy built from `git ls-files` (tracked plus untracked, honouring .gitignore), so `.git`, virtualenvs
  and ignored secrets are never copied in.
* **Scope is your change.** Without a `scope:` block in `code-shipper.yml` at the base ref, a fix may
  only edit files your branch already changes. Protected paths (CI, CODEOWNERS, rules files, secrets)
  are refused whatever the config says, as on a PR. No deletions; test-weakening is refused.
* **Your edits win.** Before anything is written back, each target file must still be byte-identical to
  what was copied. If you edited it meanwhile, that fix is refused, not merged.
* **Nothing is committed, staged or pushed.** Files are replaced in the working tree only; `git diff`
  shows exactly what changed and `git checkout -- FILE` undoes it.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..budget import RunBudget
from ..steward.classify import classify_finding
from ..steward.fix import run_fix
from ..steward.policy import ScopePolicy, protected_reason

MAX_LOCAL_FIXES = 3


@dataclass
class AppliedFix:
    title: str
    verdict: str
    applied: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "verdict": self.verdict, "applied": self.applied,
                "problems": self.problems, "verification": self.verification}


def local_scope(config: dict | None, changed_paths: list[str]) -> ScopePolicy:
    """The repo's scope block if it has one; otherwise only the files this change already touches."""
    if config:
        return ScopePolicy.from_config(config)
    allowed = tuple(sorted(p for p in changed_paths if not protected_reason(p)))
    return ScopePolicy(allowed=allowed)


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def export_tree(repo_dir: Path, target: Path) -> None:
    """Copy tracked + untracked-not-ignored files only. Symlinks are refused rather than followed."""
    listing = subprocess.run(
        ["git", "-C", str(repo_dir), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True, check=True, timeout=60,
    ).stdout.decode("utf-8", "surrogateescape")
    for rel in sorted({p for p in listing.split("\0") if p}):
        source = repo_dir / rel
        if source.is_symlink() or not source.is_file():
            continue  # deleted-but-tracked, or a link we will not follow out of the checkout
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def write_back(repo_dir: Path, fixed_root: Path, original_root: Path, paths: list[str]) -> tuple[list[str], list[str]]:
    """Replace each file in the checkout only if it is unchanged since the copy. All-or-nothing."""
    problems: list[str] = []
    for rel in paths:
        current, original = _digest(repo_dir / rel), _digest(original_root / rel)
        if current != original:
            problems.append(f"{rel} changed in your checkout while the fix ran; not overwritten")
        if (repo_dir / rel).is_symlink():
            problems.append(f"{rel} is a symlink in your checkout; not overwritten")
    if problems:
        return [], problems
    for rel in paths:
        target = repo_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.with_name(f".{target.name}.code-shipper-tmp")
        shutil.copy2(fixed_root / rel, staged)
        if (original_root / rel).exists():
            shutil.copymode(original_root / rel, staged)
        os.replace(staged, target)
    return list(paths), []


def apply_findings(
    *,
    repo_dir: Path,
    findings: list[Any],
    changed_paths: list[str],
    merge_base: str,
    worker: Any,
    scope_config: dict | None = None,
    house_rules: str = "",
    verify_recipe: str = "",
    max_fixes: int = MAX_LOCAL_FIXES,
    staging_parent: Path | None = None,
) -> list[AppliedFix]:
    """Fix Important findings one at a time, each on a fresh copy of the tree as it now stands."""
    policy = local_scope(scope_config, changed_paths)
    items = [(f, classify_finding(f)) for f in findings]
    work = [(f, item) for f, item in items if item.actionable][:max_fixes]
    results: list[AppliedFix] = []
    if staging_parent is not None:
        staging_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    budget = RunBudget(fix_rounds=max_fixes, pushes_per_day=1, wall_clock_seconds=20 * 60)  # never pushes: push=False
    for finding, item in work:
        title = str(getattr(finding, "title", "") or item.reason)
        scratch = Path(tempfile.mkdtemp(prefix="cs-local-apply-", dir=staging_parent))
        try:
            export = scratch / "export"
            export.mkdir()
            export_tree(repo_dir, export)
            receipt = run_fix(
                items=[item], repo_dir=export, base_sha=merge_base, head_sha="", head_branch="local",
                worker=worker, policy=policy, budget=budget, push=False, keep=True,
                staging_root=scratch / "staging", house_rules=house_rules, verify_recipe=verify_recipe,
            )
            fix = AppliedFix(title, receipt.verdict, problems=list(receipt.problems),
                             verification=dict(receipt.verification or {}))
            if receipt.verdict == "in_scope_not_pushed" and receipt.changed:
                fixed_root = Path(str(receipt.workspace.get("work", ""))) / "repo"
                applied, problems = write_back(repo_dir, fixed_root, export, list(receipt.changed))
                fix.applied, fix.problems = applied, fix.problems + problems
                fix.verdict = "applied" if applied else "refused"
            results.append(fix)
            if receipt.verdict == "budget_exhausted":
                break
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    return results
