"""CP4: one real end-to-end fix inside the container, pushed by the broker.

Sequence:
  1. host: clone the sandbox at origin/main, create a branch with a deliberately broken divide(),
     push it, open a PR  (the broker does the pushing, as the user, not the fixer)
  2. host: wait for CI on that branch and capture the failing output
  3. container: Hermes gets a read-only copy plus a writable workspace copy, edits only the workspace
  4. host: re-derive the diff from disk, check scope and test-weakening, run the tests
  5. host: broker commits and pushes fast-forward only, at the exact expected head, as the fixer
  6. host: wait for CI on the new head

  python3 spikes/b1/cp4_run.py            # full run
  python3 spikes/b1/cp4_run.py --json     # full receipt on stdout

Nothing is merged. The fixer token is read by path and never printed or written anywhere.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from code_shipper.contain.fixloop import check_scope, push, snapshot  # noqa: E402
from code_shipper.contain.spec import ContainerSpec  # noqa: E402
from code_shipper.contain.staging import (  # noqa: E402
    discard,
    new_run_dir,
    require_private_run_dir,
)
from code_shipper.inference.broker import Broker, read_secret_file  # noqa: E402
from code_shipper.inference.upstream import OpenAICompatUpstream  # noqa: E402

SANDBOX = "Sahil-SS9/code-shipper-sandbox"
SANDBOX_CLONE = Path.home() / "repos" / "code-shipper-sandbox"
TOKEN_PATH = Path.home() / ".config" / "code-shipper" / "tokens" / "fixer"
IMAGE = "hermes-agent-harness:latest"
UPSTREAM = "http://127.0.0.1:8091"
BROKER_SOCKET_MODE = 0o666
MAX_REQUESTS = 40
# Our own wall clock. Hermes ignores its own run budget when it is retrying.
CONTAINER_TIMEOUT_S = 420


def run(argv: list[str], *, cwd: Path | None = None, check: bool = True, env: dict | None = None):
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, env=env)
    if check and proc.returncode != 0:
        raise SystemExit(f"FAILED {' '.join(argv[:3])}: {(proc.stderr + proc.stdout)[-600:]}")
    return proc


def gh_json(*args: str):
    return json.loads(run(["gh", *args, "-R", SANDBOX]).stdout or "null")


def prepare_pr(receipt: dict) -> dict:
    """Create the broken branch and the PR on the sandbox. Broker-style: as the user, not the fixer."""
    branch = f"cp4/fix-{uuid.uuid4().hex[:8]}"
    run(["git", "fetch", "-q", "origin"], cwd=SANDBOX_CLONE)
    run(["git", "checkout", "-q", "-B", branch, "origin/main"], cwd=SANDBOX_CLONE)

    source = SANDBOX_CLONE / "calc" / "__init__.py"
    text = source.read_text()
    if "return a / b" not in text:
        raise SystemExit("sandbox main does not have the expected divide implementation")
    source.write_text(text.replace("return a / b", "return a * b  # deliberate bug"))

    run(["git", "commit", "-q", "-am", "cp4: introduce a failing divide"], cwd=SANDBOX_CLONE)
    head = run(["git", "rev-parse", "HEAD"], cwd=SANDBOX_CLONE).stdout.strip()
    run(["git", "push", "-q", "-f", "origin", branch], cwd=SANDBOX_CLONE)

    existing = gh_json("pr", "list", "--head", branch, "--state", "open", "--json", "number")
    if existing:
        number = existing[0]["number"]
    else:
        url = run(
            [
                "gh", "pr", "create", "-R", SANDBOX, "--head", branch, "--base", "main",
                "--title", "cp4: fix a failing divide",
                "--body", "Deliberately broken: divide() multiplies. A contained agent will fix it.",
            ]
        ).stdout.strip()
        number = int(url.rsplit("/", 1)[1])

    receipt.update({"branch": branch, "pr": number, "broken_head": head})
    return {"branch": branch, "pr": number, "head": head}


def wait_ci(head: str, *, timeout: int = 300) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        runs = gh_json("run", "list", "--commit", head, "--json", "status,conclusion,databaseId,name")
        if runs and runs[0]["status"] == "completed":
            return runs[0]
        time.sleep(10)
    return {"status": "timeout"}


def ci_log(run_id: int) -> str:
    proc = run(["gh", "run", "view", str(run_id), "-R", SANDBOX, "--log-failed"], check=False)
    return "\n".join((proc.stdout or proc.stderr).splitlines()[-80:])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--pr", type=int, default=0, help="reuse an existing PR number")
    parser.add_argument("--timeout", type=int, default=CONTAINER_TIMEOUT_S)
    args = parser.parse_args(argv)

    root = new_run_dir(REPO, prefix="cp4-")
    receipt: dict[str, object] = {}
    broker_server = None
    try:
        token = read_secret_file(TOKEN_PATH)  # read by path; never printed or written

        if args.pr:
            info = gh_json("pr", "view", str(args.pr), "--json", "number,headRefName,headRefOid")
            prepared = {
                "branch": info["headRefName"],
                "pr": info["number"],
                "head": info["headRefOid"],
            }
            receipt.update({"branch": prepared["branch"], "pr": prepared["pr"],
                            "broken_head": prepared["head"]})
        else:
            prepared = prepare_pr(receipt)

        first = wait_ci(prepared["head"])
        receipt["ci_before"] = first
        failure = ""
        if first.get("databaseId"):
            failure = ci_log(int(first["databaseId"]))
        receipt["ci_before_log_tail"] = failure[-2000:]

        # ---- workspace: pristine original + writable copy for the container
        original = root / "original"
        run(["git", "clone", "-q", "--depth", "1", "--branch", prepared["branch"],
             str(SANDBOX_CLONE), str(original)])
        run(["git", "checkout", "-q", prepared["head"]], cwd=original)

        source_dir = root / "source"
        work = root / "work"
        work.mkdir()
        repo_copy = work / "repo"
        subprocess.run(["cp", "-a", str(original), str(repo_copy)], check=True)
        # The worker gets no .git: it cannot commit, branch, or push even if it tries.
        subprocess.run(["rm", "-rf", str(repo_copy / ".git")], check=True)
        # The container runs as uid 65534 while the staging tree belongs to this user, so the copy
        # has to be writable by it. Only this copy: the pristine original stays owner-only.
        subprocess.run(["chmod", "-R", "a+rwX", str(repo_copy)], check=True)
        (source_dir).mkdir()
        (source_dir / "fixture.txt").write_text("synthetic-source\n")
        for path, content in (
            ("fixture.txt", "synthetic-source\n"),
            ("visible-canary.txt", "SYNTHETIC-POSITIVE-CONTROL\n"),
        ):
            (source_dir / path).write_text(content)
            (source_dir / path).chmod(0o644)

        app_dir = root / "app"
        app_dir.mkdir()
        for name, src in (
            ("canary.py", REPO / "spikes" / "b1" / "canary.py"),
            ("adapter.py", REPO / "code_shipper" / "contain" / "adapter.py"),
            ("fix_worker.py", REPO / "code_shipper" / "contain" / "fix_worker.py"),
        ):
            (app_dir / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        os.chmod(app_dir, 0o755)
        for staged in app_dir.iterdir():
            os.chmod(staged, 0o644)

        broker_dir = root / "broker"
        broker_dir.mkdir()
        os.chmod(broker_dir, 0o755)

        outside = root / "outside"
        outside.mkdir()
        for name in ("out-of-container-canary", "fake-gh-hosts.yml", "fake-fixer-token"):
            (outside / name).write_text("FAKE-CREDENTIAL-MUST-NOT-BE-REACHABLE")
            (outside / name).chmod(0o600)

        require_private_run_dir(root)
        receipt["run_dir_mode"] = oct(os.stat(root).st_mode & 0o777)

        broker = Broker(
            OpenAICompatUpstream(UPSTREAM, "sk-FAKE-cp4-synthetic-key"), ledger_path=root / "ledger.jsonl"
        )
        cap = broker.issue_capability(
            run_id=f"cp4-{uuid.uuid4().hex[:8]}",
            provider="local",
            model="auto",
            max_requests=MAX_REQUESTS,
            max_prompt_chars=120000,
            max_output_tokens=2048,
        )
        broker_server = broker.serve_socket(broker_dir / "broker.sock", mode=BROKER_SOCKET_MODE).start()
        receipt["broker"] = {"max_requests": MAX_REQUESTS, "capability_public": cap.public()}

        (work / "task.json").write_text(
            json.dumps({"failure": failure[-4000:], "timeout_s": args.timeout}), encoding="utf-8"
        )
        (work / "broker.json").write_text(
            json.dumps({"socket": "/run/cs/broker.sock", "provider": "local", "model": "auto",
                        "timeout": 180}),
            encoding="utf-8",
        )
        (work / "capability").write_text(cap.token + "\n", encoding="utf-8")
        os.chmod(work / "capability", 0o444)
        (work / "host-probe.json").write_text(
            json.dumps(
                {
                    "host_home": str(Path.home()),
                    "host_pid": os.getpid(),
                    "host_net_ns": os.readlink("/proc/self/ns/net"),
                    "outside_canary": str(outside / "out-of-container-canary"),
                    "outside_fake_gh": str(outside / "fake-gh-hosts.yml"),
                    "outside_fake_fixer": str(outside / "fake-fixer-token"),
                }
            ),
            encoding="utf-8",
        )
        os.chmod(work / "host-probe.json", 0o444)
        os.chmod(work, 0o777)

        spec = ContainerSpec(
            image=IMAGE,
            source_dir=source_dir,
            work_dir=work,
            broker_dir=broker_dir,
            app_dir=app_dir,
            entrypoint="/opt/hermes/.venv/bin/python3",
        )
        spec.validate()
        receipt["spec"] = spec.describe()
        receipt["image_id"] = run(
            ["docker", "--context", "rootless", "image", "inspect", IMAGE, "--format", "{{.Id}}"]
        ).stdout.strip()

        name = f"cs-cp4-{uuid.uuid4().hex[:10]}"
        script = (
            "import runpy,sys;"
            "canary=runpy.run_path('/app/canary.py',run_name='canary');"
            "rc=canary['main']();"
            "sys.exit(rc) if rc else runpy.run_path('/app/fix_worker.py',run_name='__main__')"
        )
        argv_container = spec.to_argv(container_name=name) + ["-c", script]
        started = time.time()
        proc = subprocess.run(argv_container, capture_output=True, text=True, timeout=args.timeout + 120)
        receipt["container"] = {
            "name": name,
            "returncode": proc.returncode,
            "seconds": round(time.time() - started, 2),
            "stdout_tail": proc.stdout[-3000:],
            "stderr_tail": proc.stderr[-2000:],
        }
        receipt["container_removed"] = not run(
            ["docker", "--context", "rootless", "ps", "-aq", "--filter", f"name=^{name}$"]
        ).stdout.strip()

        canary_file = work / "canary.json"
        receipt["canary"] = (
            json.loads(canary_file.read_text(encoding="utf-8")) if canary_file.exists() else None
        )
        fix_file = work / "fix-result.json"
        receipt["fix"] = (
            json.loads(fix_file.read_text(encoding="utf-8")) if fix_file.exists() else None
        )
        calls = work / "adapter-calls.json"
        receipt["adapter_calls"] = (
            json.loads(calls.read_text(encoding="utf-8"))["calls"] if calls.exists() else 0
        )
        log = work / "adapter.log"
        receipt["adapter_log_tail"] = (
            log.read_text(encoding="utf-8", errors="replace")[-2000:] if log.exists() else ""
        )
        # The broker's own record: request counts, prompt sizes, outcomes. Without it the receipt
        # cannot show what the model was actually asked, only that it answered.
        ledger = root / "ledger.jsonl"
        entries = (
            [json.loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else []
        )
        receipt["broker_receipts"] = entries
        receipt["confirmed_completions"] = sum(
            1 for e in entries if e.get("outcome") == "confirmed"
        )
        receipt["max_prompt_chars"] = max((e.get("prompt_chars", 0) for e in entries), default=0)

        # ---- host-side truth: what actually changed on disk
        before_snapshot = snapshot(original)
        verdict = check_scope(before_snapshot, repo_copy, original_root=original)
        receipt["scope"] = {
            "ok": verdict.ok,
            "changed": verdict.changed,
            "removed": verdict.removed,
            "problems": verdict.problems,
        }

        # Run the tests in the fixed copy before pushing anything. pytest is supplied by uv, which
        # is how the M0 spike ran them; the host interpreter has no pytest and adding one to the
        # system Python is a change this task does not get to make.
        test = subprocess.run(
            ["uv", "run", "--quiet", "--with", "pytest", "python", "-m", "pytest", "-q"],
            cwd=repo_copy, capture_output=True, text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        receipt["local_tests"] = {
            "returncode": test.returncode,
            "tail": (test.stdout or test.stderr)[-1500:],
        }
        if verdict.ok and "calc/__init__.py" in verdict.changed:
            receipt["fix_removed_the_bug"] = "return a * b" not in (
                repo_copy / "calc" / "__init__.py"
            ).read_text()

        if not verdict.ok:
            receipt["push"] = {"ok": False, "stage": "scope", "detail": verdict.problems}
        elif test.returncode != 0:
            receipt["push"] = {"ok": False, "stage": "tests", "detail": "tests still fail"}
        else:
            # git needs a real repo to push from: reattach the original .git, keep the fixed files.
            subprocess.run(["cp", "-a", str(original / ".git"), str(repo_copy / ".git")], check=True)
            # Point at the real remote, not the local clone this was copied from.
            from code_shipper.contain.fixloop import _git as _run_git

            _run_git(repo_copy, "remote", "set-url", "origin", f"https://github.com/{SANDBOX}.git")
            hooks = root / "empty-hooks"
            hooks.mkdir()
            receipt["push"] = push(
                repo_copy,
                branch=prepared["branch"],
                expected_sha=prepared["head"],
                commit_message="Fix divide(): correction by Code-Shipper (contained)\n\n"
                "Pushed by the host broker after a scope and test check.",
                token=token,
                hookless_dir=hooks,
                author=("Code-Shipper (bot)", "noreply@example.invalid"),
            )

        if receipt["push"].get("ok"):
            second = wait_ci(receipt["push"]["new_sha"])
            receipt["ci_after"] = second
            receipt["ci_after_log"] = (
                ci_log(int(second["databaseId"]))[-1500:] if second.get("databaseId") else ""
            )
        receipt["merged"] = False

        receipt["verdict"] = "PASS" if _verdict(receipt) else "FAIL"
        (root / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        if args.json:
            print(json.dumps(receipt, indent=2))
        else:
            _summary(receipt)
        return 0 if receipt["verdict"] == "PASS" else 1
    finally:
        if broker_server is not None:
            broker_server.stop()
        if args.keep:
            print(f"kept: {root}", file=sys.stderr)
        else:
            discard(root, root.parent)


def _verdict(receipt: dict) -> bool:
    canary = receipt.get("canary") or {}
    if canary.get("failed") or not canary.get("checks"):
        return False
    if not receipt.get("container_removed"):
        return False
    scope = receipt.get("scope") or {}
    if not scope.get("ok"):
        return False
    if not receipt.get("fix_removed_the_bug"):
        return False
    tests = receipt.get("local_tests") or {}
    if tests.get("returncode") != 0:
        return False
    if receipt["push"].get("ok") is not True:
        return False
    after = receipt.get("ci_after") or {}
    if after.get("conclusion") != "success":
        return False
    return receipt.get("merged") is False


def _summary(receipt: dict) -> None:
    print(f"pr               #{receipt.get('pr')} branch {receipt.get('branch')}")
    print(f"broken head      {str(receipt.get('broken_head'))[:12]}")
    before = receipt.get("ci_before") or {}
    print(f"CI before        {before.get('conclusion', before.get('status'))}")
    container = receipt.get("container") or {}
    print(f"container rc     {container.get('returncode')} in {container.get('seconds')}s")
    canary = receipt.get("canary") or {}
    print(f"canary           {canary.get('passed', 0)} passed, {len(canary.get('failed') or [])} failed")
    print(f"adapter calls    {receipt.get('adapter_calls')} (ceiling {MAX_REQUESTS})")
    scope = receipt.get("scope") or {}
    print(f"scope            {'ok' if scope.get('ok') else 'REFUSED'} {scope.get('changed')}")
    for problem in scope.get("problems") or []:
        print(f"  PROBLEM        {problem}")
    print(f"local tests      rc {(receipt.get('local_tests') or {}).get('returncode')}")
    pushed = receipt.get("push") or {}
    print(f"push             {'ok' if pushed.get('ok') else 'FAILED'} {str(pushed.get('detail'))[:120]}")
    after = receipt.get("ci_after") or {}
    print(f"CI after         {after.get('conclusion', after.get('status', 'n/a'))}")
    print(f"merged           {receipt.get('merged')}")
    print(f"VERDICT          {receipt.get('verdict')}")


if __name__ == "__main__":
    raise SystemExit(main())
