"""CP3 harness: one real model call from inside the container, through the adapter and the broker.

Differences from the CP2 harness that matter:

  * image is hermes-agent-harness:latest, pinned by image ID, never pulled
  * the worker is a real `hermes chat` one-shot, not a stand-in
  * the upstream is local Turbofit at 127.0.0.1:8091 (model "auto"), so nothing leaves the machine
    and nothing is billed
  * HERMES_HOME is a disposable tmpfs; the image's /root/.hermes is never used
  * the run directory's permissions are re-verified immediately before the broker binds

  python3 spikes/b1/cp3_run.py                    # normal: one real completion
  python3 spikes/b1/cp3_run.py --stop-broker-before-run
  python3 spikes/b1/cp3_run.py --stop-broker-mid-run
  python3 spikes/b1/cp3_run.py --json             # full receipt on stdout
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from code_shipper.contain.spec import ContainerSpec, workspace_paths  # noqa: E402
from code_shipper.contain.staging import (  # noqa: E402
    discard,
    new_run_dir,
    require_private_run_dir,
)
from code_shipper.inference.broker import Broker, StartupError, read_secret_file  # noqa: E402
from code_shipper.inference.upstream import OpenAICompatUpstream  # noqa: E402

IMAGE = "hermes-agent-harness:latest"
UPSTREAM_DEFAULT = "http://127.0.0.1:8091"
MODEL_DEFAULT = "auto"
# The broker's own request ceiling for this run. Small on purpose: one bounded session.
MAX_REQUESTS = 8
SOCKET_MODE = 0o666  # see the report: the run directory 0700 is the real control


def image_id(image: str) -> str:
    return subprocess.check_output(
        ["docker", "--context", "rootless", "image", "inspect", image, "--format", "{{.Id}}"],
        text=True,
    ).strip()


def build_tree(root: Path) -> tuple[Path, Path, Path, Path]:
    """Source, workspace, broker dir and app dir. The source is synthetic: CP3 proves the channel."""
    source, work = workspace_paths(root)
    (source / "fixture.txt").write_text("synthetic-source\n", encoding="utf-8")
    (source / "visible-canary.txt").write_text("SYNTHETIC-POSITIVE-CONTROL\n", encoding="utf-8")
    for path in (source / "fixture.txt", source / "visible-canary.txt"):
        path.chmod(0o644)

    outside = root / "outside"
    outside.mkdir()
    (outside / "fake-gh-hosts.yml").write_text("FAKE-CREDENTIAL-MUST-NOT-BE-REACHABLE")
    (outside / "fake-fixer-token").write_text("FAKE-CREDENTIAL-MUST-NOT-BE-REACHABLE")
    (outside / "out-of-container-canary").write_text("FAKE-CREDENTIAL-MUST-NOT-BE-REACHABLE")
    for path in outside.iterdir():
        path.chmod(0o600)

    broker_dir = root / "broker"
    broker_dir.mkdir()
    os.chmod(broker_dir, 0o755)

    app_dir = root / "app"
    app_dir.mkdir()
    for name, src in (
        ("canary.py", REPO / "spikes" / "b1" / "canary.py"),
        ("adapter.py", REPO / "code_shipper" / "contain" / "adapter.py"),
        ("hermes_worker.py", REPO / "code_shipper" / "contain" / "hermes_worker.py"),
    ):
        (app_dir / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    os.chmod(app_dir, 0o755)
    for staged in app_dir.iterdir():
        os.chmod(staged, 0o644)

    return source, work, broker_dir, app_dir


def start_broker(broker_dir: Path, ledger: Path, *, provider: str, model: str, url: str, key: str):
    broker = Broker(OpenAICompatUpstream(url, key, required=False), ledger_path=ledger)
    cap = broker.issue_capability(
        run_id=f"cp3-{uuid.uuid4().hex[:8]}",
        provider=provider,
        model=model,
        max_requests=MAX_REQUESTS,
        max_prompt_chars=64000,
        max_output_tokens=1024,
    )
    server = broker.serve_socket(broker_dir / "broker.sock", mode=SOCKET_MODE).start()
    return broker, server, cap


def container_argv(spec: ContainerSpec, *, name: str, with_canary: bool) -> list[str]:
    argv = spec.to_argv(container_name=name)
    if with_canary:
        # Run the canary set first, then the real Hermes worker, in one process so a canary failure
        # stops the run before any model call happens.
        script = (
            "import runpy,sys;"
            "canary=runpy.run_path('/app/canary.py',run_name='canary');"
            "rc=canary['main']();"
            "sys.exit(rc) if rc else runpy.run_path('/app/hermes_worker.py',run_name='__main__')"
        )
        argv = argv[:-len(spec.command)] + ["-c", script] if spec.command else argv + ["-c", script]
    return argv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--provider", default="local")
    parser.add_argument("--model", default=MODEL_DEFAULT)
    parser.add_argument("--upstream-url", default=UPSTREAM_DEFAULT)
    parser.add_argument("--credential-file", default="", help="file holding the provider key")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--stop-broker-before-run", action="store_true")
    parser.add_argument("--stop-broker-mid-run", action="store_true")
    parser.add_argument("--no-canary", action="store_true", help="skip the canary prefix")
    args = parser.parse_args(argv)

    root = new_run_dir(REPO, prefix="cp3-")
    name = f"cs-cp3-{uuid.uuid4().hex[:10]}"
    ledger = root / "receipts.jsonl"
    receipt: dict[str, object] = {"scratch": str(root), "container": name}

    server = None
    try:
        source, work, broker_dir, app_dir = build_tree(root)

        # Synthetic credential by default: Turbofit ignores the key, and CP3 must not need a real one.
        if args.credential_file:
            key = read_secret_file(args.credential_file)
            receipt["credential"] = "from-file"
        else:
            key = "sk-FAKE-cp3-synthetic-key-0000"
            receipt["credential"] = "synthetic"

        # Guard the run directory again, immediately before the broker binds a socket inside it.
        require_private_run_dir(root)
        receipt["run_dir_mode"] = oct(os.stat(root).st_mode & 0o777)
        receipt["run_dir_uid"] = os.stat(root).st_uid
        receipt["socket_mode"] = oct(SOCKET_MODE)

        broker, server, cap = start_broker(
            broker_dir, ledger,
            provider=args.provider, model=args.model,
            url=args.upstream_url, key=key,
        )
        receipt["broker"] = {
            "socket": str(broker_dir / "broker.sock"),
            "capability_public": cap.public(),
            "max_requests": MAX_REQUESTS,
        }

        (work / "task.json").write_text(
            json.dumps(
                {"prompt": "Reply with exactly one word: pong. No punctuation, no explanation."}
            ),
            encoding="utf-8",
        )
        (work / "broker.json").write_text(
            json.dumps(
                {
                    "socket": "/run/cs/broker.sock",
                    "provider": args.provider,
                    "model": args.model,
                    "timeout": 180,
                }
            ),
            encoding="utf-8",
        )
        (work / "capability").write_text(cap.token + "\n", encoding="utf-8")
        os.chmod(work / "capability", 0o444)

        host_probe = {
            "host_home": str(Path.home()),
            "host_pid": os.getpid(),
            "host_net_ns": os.readlink("/proc/self/ns/net"),
            "outside_canary": str(root / "outside" / "out-of-container-canary"),
            "outside_fake_gh": str(root / "outside" / "fake-gh-hosts.yml"),
            "outside_fake_fixer": str(root / "outside" / "fake-fixer-token"),
        }
        (work / "host-probe.json").write_text(json.dumps(host_probe), encoding="utf-8")
        os.chmod(work / "host-probe.json", 0o444)
        os.chmod(work, 0o777)

        spec = ContainerSpec(
            image=IMAGE,
            source_dir=source,
            work_dir=work,
            broker_dir=broker_dir,
            app_dir=app_dir,
            entrypoint="/opt/hermes/.venv/bin/python3",
        )
        spec.validate()
        receipt["spec"] = spec.describe()
        receipt["image_id"] = image_id(IMAGE)

        if args.stop_broker_before_run:
            server.stop()
            server = None
            receipt["broker_stopped_before_run"] = True

        cmd = container_argv(spec, name=name, with_canary=not args.no_canary)

        if args.stop_broker_mid_run:
            (work / "task.json").write_text(
                json.dumps(
                    {
                        "prompt": "Reply with exactly one word: pong. No punctuation, no explanation.",
                        "delay_s": 25,
                    }
                ),
                encoding="utf-8",
            )
            cmd = container_argv(spec, name=name, with_canary=not args.no_canary)
            started = time.time()
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(18)  # canaries done, Hermes up and about to call
            assert server is not None
            server.stop()
            server = None
            receipt["broker_stopped_mid_run"] = True
            out, err = proc.communicate(timeout=args.timeout)
            run = {
                "returncode": proc.returncode,
                "stdout": out,
                "stderr": err,
                "seconds": round(time.time() - started, 2),
            }
        else:
            started = time.time()
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout)
            run = {
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "seconds": round(time.time() - started, 2),
            }

        receipt["run"] = {
            "returncode": run["returncode"],
            "seconds": run["seconds"],
            "stdout_tail": run["stdout"][-4000:],
            "stderr_tail": run["stderr"][-3000:],
        }
        receipt["container_removed"] = container_absent(name)

        canary_file = work / "canary.json"
        receipt["canary"] = (
            json.loads(canary_file.read_text(encoding="utf-8")) if canary_file.exists() else None
        )
        hermes_result = work / "hermes-result.json"
        receipt["hermes"] = (
            json.loads(hermes_result.read_text(encoding="utf-8")) if hermes_result.exists() else None
        )
        calls_file = work / "adapter-calls.json"
        receipt["adapter_calls"] = (
            json.loads(calls_file.read_text(encoding="utf-8"))["calls"] if calls_file.exists() else 0
        )

        entries = (
            [json.loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else []
        )
        receipt["broker_receipts"] = entries
        receipt["confirmed_completions"] = sum(1 for e in entries if e.get("outcome") == "confirmed")

        ledger_text = ledger.read_text() if ledger.exists() else ""
        receipt["ledger_has_key"] = key in ledger_text
        receipt["ledger_has_prompt"] = "pong" in ledger_text.lower()

        receipt["verdict"] = "PASS" if _verdict(receipt) else "FAIL"
        out_path = root / "receipt.json"
        out_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

        if args.json:
            print(json.dumps(receipt, indent=2))
        else:
            _summary(receipt)
        return 0 if receipt["verdict"] == "PASS" else 1
    finally:
        if server is not None:
            server.stop()
        if args.keep:
            print(f"kept: {root}", file=sys.stderr)
        else:
            discard(root, root.parent)


def container_absent(name: str) -> bool:
    out = subprocess.check_output(
        ["docker", "--context", "rootless", "ps", "-aq", "--filter", f"name=^{name}$"], text=True
    ).strip()
    return not out


def _verdict(receipt: dict) -> bool:
    if receipt.get("broker_stopped_before_run") or receipt.get("broker_stopped_mid_run"):
        return _fail_closed_verdict(receipt)

    canary = receipt.get("canary") or {}
    if canary.get("failed") or not canary.get("checks"):
        return False
    if not receipt.get("container_removed"):
        return False
    if receipt.get("ledger_has_key") or receipt.get("ledger_has_prompt"):
        return False

    run = receipt.get("run") or {}
    if run.get("returncode") != 0:
        return False

    # the actual claim: a real model answered, inside the container, through the broker
    if receipt.get("confirmed_completions", 0) < 1:
        return False
    hermes = receipt.get("hermes") or {}
    if "pong" not in str(hermes.get("hermes", {}).get("stdout", "")).lower():
        return False
    # and it stayed inside the budget
    return 0 < receipt.get("adapter_calls", 0) <= MAX_REQUESTS


def _fail_closed_verdict(receipt: dict) -> bool:
    run = receipt.get("run") or {}
    if run.get("returncode") == 0:
        return False
    if receipt.get("confirmed_completions"):
        return False
    receipt_hermes = receipt.get("hermes") or {}
    if receipt_hermes and "pong" in str(receipt_hermes.get("hermes", {}).get("stdout", "")).lower():
        return False

    canary = receipt.get("canary") or {}
    checks = canary.get("checks") or []
    if not checks and not receipt.get("no_canary"):
        return False

    if receipt.get("broker_stopped_before_run"):
        # the socket genuinely does not exist: that canary is expected to fail
        failed = canary.get("failed") or []
        if failed != ["broker socket present"]:
            return False
        return True

    # mid-run: the socket existed when the run began, so every canary must have passed
    if canary.get("failed"):
        return False
    return True


def _summary(receipt: dict) -> None:
    print(f"container        {receipt['container']}")
    print(f"image            {receipt.get('image_id')}")
    print(f"run dir / socket {receipt.get('run_dir_mode')} owned {receipt.get('run_dir_uid')}, "
          f"socket {receipt.get('socket_mode')}")
    run = receipt.get("run") or {}
    print(f"run rc           {run.get('returncode')} in {run.get('seconds')}s")
    canary = receipt.get("canary") or {}
    print(f"canary           {canary.get('passed', 0)} passed, {len(canary.get('failed') or [])} failed")
    for failed in canary.get("failed") or []:
        print(f"  FAILED         {failed}")
    print(f"adapter calls    {receipt.get('adapter_calls')} (broker ceiling {MAX_REQUESTS})")
    print(f"completions      {receipt.get('confirmed_completions')}")
    hermes = (receipt.get("hermes") or {}).get("hermes") or {}
    print(f"hermes stdout    {hermes.get('stdout', '').strip()[:80]!r}")
    print(f"container gone   {receipt.get('container_removed')}")
    print(f"VERDICT          {receipt.get('verdict')}")


if __name__ == "__main__":
    raise SystemExit(main())
