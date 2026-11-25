"""P3-B part 2: one real contained fix run, end to end (FR-S1–S5).

Runs the whole path for real against a sandbox PR:
  clone the head branch
  classify the failing check
  prepare the contained workspace (0700, private, disposable)
  run Hermes inside the container with --network none, through the broker
  judge scope on the host from the files on disk
  push fast-forward only, at the exact expected head, to the PR head branch

Nothing here can merge or approve. The broker holds the credential; the container never sees it.

Usage:
    python3 spikes/b1/p3b_run.py --pr 23 --json

Writes a receipt to docs/reports/evidence/P3B/receipt.json when --json is given with no stdout
redirection, and always prints it.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from code_shipper.budget import RunBudget
from code_shipper.contain import fixloop
from code_shipper.steward import classify as classify_mod
from code_shipper.steward.classify import WorkKind
from code_shipper.steward.fix import run_fix
from code_shipper.steward.policy import ScopePolicy
from code_shipper.steward.workspace import Workspace

SANDBOX = "Sahil-SS9/code-shipper-sandbox"
TOKEN_PATH = Path.home() / ".config" / "code-shipper" / "tokens" / "fixer"
IMAGE = "hermes-agent-harness:latest"
REPORT_DIR = Path(__file__).resolve().parents[2] / "docs/reports/evidence/P3B"

# The scope the sandbox repository declares for itself. Nothing about `calc` is hard-coded in the
# product; this is the repository's own policy, kept here so the run is reproducible.
SANDBOX_POLICY = ScopePolicy(
    allowed=("calc/**/*.py", "tests/**/*.py"),
    forbidden=(".github/**", "**/setup.cfg", "**/pyproject.toml", "**/conftest.py",
               "**/.code-shipper/**"),
    max_files=5,
)


def sh(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
                          check=check)


def gh_json(*args: str) -> dict:
    out = sh(["gh", "api", *args]).stdout
    return json.loads(out or "{}")


def read_token() -> str:
    """The credential is read by path and never printed."""
    return TOKEN_PATH.read_text().strip()


class ContainedWorker:
    """Runs Hermes inside the container, through the broker, and reports what it changed."""

    def __init__(self, image: str = IMAGE, repo_root: Path | None = None) -> None:
        self.image = image
        self.repo_root = repo_root
        self.last_result: dict = {}

    def run(self, *, workspace: Workspace, item, prompt: str, budget: RunBudget) -> dict:
        from code_shipper.contain.runner import run_in_container

        budget.check_clock()
        result = run_in_container(
            image=self.image,
            work_dir=workspace.work,
            source_dir=workspace.pristine,
            broker_dir=workspace.broker_dir,
            prompt=prompt,
            repo_root=self.repo_root,
        )
        self.last_result = result.as_dict()
        budget.record_cost(None)  # local loopback model: cost is unknown, recorded as unknown
        return {"changed": result.changed, "removed": result.removed,
                "returncode": result.returncode, "ok": result.ok}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-push", action="store_true", help="stop after the scope check")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    receipt: dict = {"pr": args.pr, "sandbox": SANDBOX, "started": time.time()}

    pr = gh_json(f"repos/{SANDBOX}/pulls/{args.pr}")
    head_sha = pr["head"]["sha"]
    head_branch = pr["head"]["ref"]
    base_sha = pr["base"]["sha"]
    receipt.update({"head_before": head_sha, "head_branch": head_branch, "base": base_sha,
                    "title": pr.get("title", "")})
    print(f"PR #{args.pr} {head_branch} @ {head_sha[:12]}")

    # CI before
    runs = json.loads(sh(["gh", "run", "list", "-R", SANDBOX, "--branch", head_branch,
                          "--limit", "1", "--json", "databaseId,conclusion,status"]).stdout or "[]")
    if runs:
        receipt["ci_before"] = runs[0]
        print(f"CI before: run {runs[0]['databaseId']} {runs[0]['conclusion']}")

    # The failing check's log is what makes classification possible.
    checks = json.loads(sh(["gh", "pr", "checks", str(args.pr), "-R", SANDBOX, "--json",
                            "name,state,link"]).stdout or "[]")
    log_tail = ""
    failing = [c for c in checks if c.get("state", "").upper() not in ("SUCCESS", "PENDING", "")
               or c.get("state", "").upper() == "FAILURE"]
    for check in failing:
        link = check.get("link") or ""
        if "/job/" in link:
            job = link.rsplit("/", 1)[-1]
            proc = sh(["gh", "api", f"repos/{SANDBOX}/actions/jobs/{job}/logs"], check=False)
            log_tail = (proc.stdout or "")[-8000:]
            break
    receipt["log_tail_chars"] = len(log_tail)

    # Files the PR changes, so classification can tell whether the failure is ours.
    files = json.loads(sh(["gh", "api", f"repos/{SANDBOX}/pulls/{args.pr}/files",
                           "--paginate"]).stdout or "[]")
    changed = [f["filename"] for f in files]

    class Check:
        def __init__(self, name: str, conclusion: str, log: str) -> None:
            self.name = name
            self.conclusion = conclusion
            self.log_tail = log
            self.files: list[str] = []
            # Set by the caller once the test files have been resolved from the checkout.
            self.test_imports: list[str] = []

    class Pr:
        def __init__(self, number: int) -> None:
            self.number = number

    # Work from a fresh clone of the branch, so test imports can be read from the real files.
    # Not under ~/.hermes: the spec refuses to mount anything credential-looking, and that guard is
    # right. The repo's .staging is the real location a run uses.
    staging = Path(__file__).resolve().parents[2] / ".staging"
    staging.mkdir(exist_ok=True)
    work_root = Path(tempfile.mkdtemp(prefix="p3b-", dir=staging))
    receipt["work_root"] = str(work_root)
    repo = work_root / "repo"
    sh(["git", "clone", "-q", "--branch", head_branch, "--single-branch",
        f"https://github.com/{SANDBOX}.git", str(repo)])

    # The failure's visible location is a test file, which a source-only PR does not touch. Its real
    # imports are the evidence that the failure belongs to this change.
    test_imports: list[str] = []
    for named_test in classify_mod.imports_of(log_tail):
        candidate = repo / named_test
        if candidate.exists():
            test_imports.extend(
                classify_mod.parse_imports(candidate.read_text(encoding="utf-8", errors="replace"))
            )
    receipt["test_imports"] = test_imports

    check_objs = [
        Check(c.get("name", "pytest"), c.get("state", "").lower(), log_tail) for c in checks
    ]
    for check in check_objs:
        check.test_imports = test_imports

    items = classify_mod.classify(
        pr=Pr(args.pr),
        mergeable_state=(pr.get("mergeable_state") or "").lower(),
        checks=check_objs,
        findings=[],
        comments=[],
        changed_files=changed,
    )
    receipt["classification"] = [i.as_dict() for i in items]
    print("classification:", [(i.kind, i.actionable) for i in items])

    token = read_token()
    worker = ContainedWorker(repo_root=Path(__file__).resolve().parents[2])

    result = run_fix(
        items=items,
        repo_dir=repo,
        base_sha=base_sha,
        head_sha=head_sha,
        head_branch=head_branch,
        worker=worker,
        policy=SANDBOX_POLICY,
        budget=RunBudget(fix_rounds=1, pushes_per_day=1, wall_clock_seconds=15 * 60),
        token=None if args.no_push else token,
        staging_root=work_root / "staging",
        push=not args.no_push,
        keep=args.keep,
        remote=f"https://github.com/{SANDBOX}.git",
        commit_message=(
            "Fix divide() to return a number, not a labelled string\n\n"
            "The test asserts divide(6, 3) == 2, so the return value must stay numeric.\n"
            "Pushed by the host broker after a scope check."
        ),
    )
    receipt["fix"] = result.as_dict()
    if args.keep and result.workspace.get("root"):
        ws_root = Path(result.workspace["root"])
        receipt["kept_workspace"] = str(ws_root)
        checkout = ws_root / "work" / "repo"
        if checkout.exists():
            import subprocess as _sp
            receipt["kept_diff"] = _sp.run(
                ["git", "-c", f"safe.directory={checkout}", "status", "--porcelain"],
                cwd=str(checkout), capture_output=True, text=True,
            ).stdout
    receipt["worker"] = worker.last_result
    print(f"fix: ok={result.ok} stage={result.stage} verdict={result.verdict}")
    if result.problems:
        print("problems:", result.problems)

    # CI after, if a push happened.
    if result.ok and result.push.get("ok"):
        new_sha = result.push.get("new_sha", "")
        receipt["head_after"] = new_sha
        print(f"pushed {head_sha[:12]} -> {new_sha[:12]}")
        deadline = time.time() + 420
        while time.time() < deadline:
            runs = json.loads(sh(["gh", "run", "list", "-R", SANDBOX, "--branch", head_branch,
                                  "--limit", "1", "--json", "databaseId,conclusion,status"]
                                 ).stdout or "[]")
            if runs and runs[0]["status"] == "completed":
                receipt["ci_after"] = runs[0]
                print(f"CI after: run {runs[0]['databaseId']} {runs[0]['conclusion']}")
                break
            time.sleep(15)

    receipt["finished"] = time.time()
    receipt["duration_s"] = round(receipt["finished"] - receipt["started"], 1)

    if not args.keep:
        shutil.rmtree(work_root, ignore_errors=True)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True))

    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt.get("fix", {}).get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
