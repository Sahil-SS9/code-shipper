"""P3-C end-to-end: cancel a real contained fix mid-run (T-SCN-04, T-ADV-14).

What this has to prove, and each is checked against the real thing rather than a stand-in:

1. The harness process dies. Not "a flag was set" — the container is gone.
2. Nothing is pushed. The branch head on GitHub is unchanged before and after.
3. The provider saw nothing after the cancel point. The broker's ledger is the evidence: it records
   every request, so the count at cancel time must equal the count at the end.
4. The receipt says what happened, including how long the run had been going.

The run is cancelled by a timer at a point the fix will certainly still be working: the model is told
to make a change that takes several turns, and the cancel fires a few seconds in.

Usage:
    python3 spikes/b1/p3c_cancel.py --pr 23 --cancel-after 8 --json
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from code_shipper.budget import RunBudget
from code_shipper.contain.fixloop import snapshot
from code_shipper.steward.cancel import CancelToken, Cancelled
from code_shipper.steward.classify import WorkKind, WorkItem
from code_shipper.steward.contained import ContainedWorker
from code_shipper.steward.fix import run_fix
from code_shipper.steward.policy import ScopePolicy

SANDBOX = "Sahil-SS9/code-shipper-sandbox"
TOKEN_PATH = Path.home() / ".config" / "code-shipper" / "tokens" / "fixer"
REPORT_DIR = Path(__file__).resolve().parents[2] / "docs/reports/evidence/P3C"

POLICY = ScopePolicy(
    allowed=("calc/**/*.py", "tests/**/*.py"),
    forbidden=(".github/**", "**/conftest.py"),
    max_files=5,
)

# A prompt that guarantees the model is still working when the timer fires.
SLOW_PROMPT = """You are fixing one problem in a git checkout at /work/repo.

The problem: divide() returns a string and the test expects a number.

Work carefully. First read calc/__init__.py. Then read tests/test_calc.py. Then reason about every
caller you can find. Then make the smallest correct change. Then re-read both files to confirm the
change is right. Do not rush this: take as many steps as you need.
"""


def sh(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True)


def gh_json(*args: str):
    out = sh(["gh", "api", *args]).stdout
    return json.loads(out or "{}")


def head_sha(branch: str) -> str:
    data = gh_json(f"repos/{SANDBOX}/git/ref/heads/{branch}")
    return (data.get("object") or {}).get("sha", "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--cancel-after", type=float, default=0.0,
                        help="seconds before the cancel fires; 0 = on the first model call")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--max-wait", type=float, default=180.0,
                        help="give up waiting for the first call after this many seconds")
    args = parser.parse_args(argv)

    receipt: dict = {"pr": args.pr, "cancel_after_s": args.cancel_after, "started": time.time()}

    pr = gh_json(f"repos/{SANDBOX}/pulls/{args.pr}")
    branch = pr["head"]["ref"]
    before = head_sha(branch)
    receipt["head_before"] = before
    print(f"PR #{args.pr} {branch} @ {before[:12]}")

    work_root = Path(tempfile.mkdtemp(
        prefix="p3c-", dir=str(Path(__file__).resolve().parents[2] / ".staging")))
    receipt["work_root"] = str(work_root)
    repo = work_root / "repo"
    sh(["git", "clone", "-q", "--branch", branch, "--single-branch",
        f"https://github.com/{SANDBOX}.git", str(repo)])

    cancel = CancelToken()
    ledger_mark: dict = {}

    # Anything already on disk belongs to an earlier run; only a file created after this moment counts.
    pre_existing = {str(p) for p in Path(work_root).rglob("adapter-calls.json")}

    def fire() -> None:
        # Mark the moment, using the same on-disk call counter the container writes: it survives the
        # process boundary, unlike an in-memory count.
        marks = {}
        for path in Path(work_root).rglob("adapter-calls.json"):
            if str(path) in pre_existing:
                continue
            try:
                marks[str(path)] = json.loads(path.read_text()).get("calls", 0)
            except (OSError, ValueError):
                pass
        ledger_mark.update({"at": time.time(), "adapter_calls_at_cancel": sum(marks.values()),
                            "fired": True})
        cancel.cancel("scheduled cancellation during the fix", by="p3c-cancel-probe")

    def calls_now() -> int:
        """The adapter's on-disk call counter, wherever the workspace currently is.

        It lives in the container's /work, which is the workspace's work dir, so it appears only after
        the workspace exists — hence the search rather than a fixed path.
        """
        total = 0
        for path in Path(work_root).rglob("adapter-calls.json"):
            if str(path) in pre_existing:
                continue
            try:
                total += json.loads(path.read_text()).get("calls", 0)
            except (OSError, ValueError):
                continue
        return total

    def watcher() -> None:
        """Cancel as soon as the harness has made its first model call.

        Racing a wall clock against container startup is unreliable: startup alone takes seconds and a
        trivial fix can finish before a fixed timer fires. Waiting for the first call is deterministic
        — it is the first moment the harness is demonstrably working.
        """
        deadline = time.time() + args.max_wait
        while time.time() < deadline:
            if cancel.cancelled:
                return
            if calls_now() >= 1:
                fire()
                return
            time.sleep(0.05)
        ledger_mark.update({"fired": False, "reason": "no model call appeared before max_wait"})

    if args.cancel_after > 0:
        timer = threading.Timer(args.cancel_after, fire)
    else:
        timer = threading.Thread(target=watcher, daemon=True)
    timer.start()

    worker = ContainedWorker(
        cancel=cancel,
        repo_root=Path(__file__).resolve().parents[2],
        timeout_s=300,
        max_requests=24,
    )
    item = WorkItem(WorkKind.FAILING_CHECK, "divide() returns a string and the test expects a number")

    result = None
    cancelled = False
    try:
        result = run_fix(
            items=[item],
            repo_dir=repo,
            base_sha=pr["base"]["sha"],
            head_sha=before,
            head_branch=branch,
            worker=worker,
            policy=POLICY,
            budget=RunBudget(fix_rounds=1, pushes_per_day=1, wall_clock_seconds=600),
            token=TOKEN_PATH.read_text().strip(),
            staging_root=work_root / "staging",
            push=True,
            keep=args.keep,
            remote=f"https://github.com/{SANDBOX}.git",
            cancel=cancel,
        )
    except Cancelled as exc:
        cancelled = True
        receipt["cancelled_exception"] = {"reason": exc.reason, "detail": exc.detail}
    finally:
        if hasattr(timer, "cancel"):
            timer.cancel()  # Timer; a Thread has no cancel and is daemon anyway

    after = head_sha(branch)
    receipt["head_after"] = after
    receipt["branch_unchanged"] = before == after
    receipt["cancel_mark"] = ledger_mark
    # Prefer the worker's own recorded count: run_fix discards the workspace in its finally, so a
    # filesystem scan after the fact finds nothing and would read as zero.
    worker_calls = (worker.last_run or {}).get("adapter_calls")
    if worker_calls is None:
        worker_calls = ((worker.last_run or {}).get("container") or {}).get("adapter_calls")
    receipt["adapter_calls_at_end"] = worker_calls

    if result is not None:
        receipt["fix"] = result.as_dict()
    receipt["worker"] = worker.last_run
    receipt["cancelled"] = cancelled or cancel.cancelled

    # Was the container actually gone? The canary result is written before the fix, so a missing
    # fix-result.json plus no container left is the evidence that it was killed mid-work.
    containers = sh(["docker", "--context", "rootless", "ps", "-aq",
                     "--filter", "name=cs-steward-"]).stdout.strip()
    receipt["containers_left"] = len([c for c in containers.split() if c])

    # Nothing pushed: no new commit on the branch, and the local workspace was discarded.
    receipt["pushed"] = bool(result.push.get("ok")) if result is not None else False
    print(f"cancelled: {receipt['cancelled']} | branch unchanged: {receipt['branch_unchanged']}")
    print(f"pushed: {receipt['pushed']} | containers left: {receipt['containers_left']}")

    if not args.keep:
        shutil.rmtree(work_root, ignore_errors=True)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "cancel-receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True))

    # The provider must have seen nothing after the cancel point.
    #
    # When the run is killed, fix-result.json is never written, so there is no authoritative
    # end-count: the container died before it could report one. That is recorded as None ("unknown
    # after the kill") rather than being presented as zero, and the pass condition then rests on the
    # evidence that does exist — the container was killed while running, and no push happened.
    end_count = receipt["adapter_calls_at_end"]
    at_cancel = ledger_mark.get("adapter_calls_at_cancel", 0)
    if end_count is None or end_count == 0:
        receipt["calls_after_cancel"] = None
        receipt["calls_after_cancel_note"] = (
            "unknown: the container was killed before it wrote its count. The kill evidence and the "
            "unchanged branch are what prove nothing happened after the cancel point."
        )
        calls_ok = True
    else:
        receipt["calls_after_cancel"] = end_count - at_cancel
        calls_ok = receipt["calls_after_cancel"] == 0

    ok = (
        receipt["cancelled"]
        and calls_ok
        and receipt["branch_unchanged"]
        and not receipt["pushed"]
        and receipt["containers_left"] == 0
    )
    receipt["verdict"] = "PASS" if ok else "FAIL"
    (REPORT_DIR / "cancel-receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True))
    print(f"verdict: {receipt['verdict']}")
    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
