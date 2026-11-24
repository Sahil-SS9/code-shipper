"""CP2 harness: run the contained worker against a stand-in agent and prove the canaries.

What this does, in order:
  1. builds a scratch tree: read-only synthetic "source", writable workspace, a socket directory
  2. writes synthetic canary files OUTSIDE the mounts (fake values only)
  3. starts the host broker on a socket inside the socket directory
  4. runs the container with the spec from code_shipper.contain.spec
  5. collects canary results, the worker result, and the broker receipt
  6. removes the container and stops the broker

Nothing real is mounted or read: every credential-shaped path used here is synthetic.

  python3 spikes/b1/cp2_run.py            # full run + receipt
  python3 spikes/b1/cp2_run.py --json     # receipt on stdout only

CP2 uses a stand-in agent (`--agent standin`) that makes one broker call. CP3 runs the same
harness with `--agent hermes`, which shells a real one-shot in place of the stand-in.
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
import threading
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from code_shipper.contain.spec import ContainerSpec, workspace_paths  # noqa: E402
from code_shipper.contain.staging import discard, new_run_dir  # noqa: E402
from code_shipper.inference.broker import Broker, StartupError, read_secret_file  # noqa: E402
from code_shipper.inference.upstream import OpenAICompatUpstream  # noqa: E402

CANARY_VALUE = "FAKE-CREDENTIAL-MUST-NOT-BE-REACHABLE"
IMAGE_PYTHON = "slopsearx-kensei:local"          # cached; python3.12 present
IMAGE_HERMES = "hermes-agent-harness:latest"     # cached; used by CP3


class FakeProvider:
    """A loopback OpenAI-compatible endpoint. No credential, no network, no cost.

    CP2 needs the channel to WORK end to end, not to reach a real model. This stands in for the
    provider so CP2 can be repeated without a key or spend; CP3 replaces it with the real one.
    """

    def __init__(self) -> None:
        from http.server import BaseHTTPRequestHandler, HTTPServer

        provider = self
        self.hits: list[dict[str, str | None]] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                provider.hits.append(
                    {
                        "authorization": self.headers.get("Authorization"),
                        "model": body.get("model"),
                        "path": self.path,
                    }
                )
                payload = {
                    "choices": [{"message": {"role": "assistant", "content": "pong"}}],
                    "usage": {"total_tokens": 7},
                }
                raw = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> "FakeProvider":
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def image_id(image: str) -> str:
    out = subprocess.check_output(
        ["docker", "--context", "rootless", "image", "inspect", image, "--format", "{{.Id}}"],
        text=True,
    )
    return out.strip()


def build_tree(root: Path) -> tuple[Path, Path, Path]:
    source, work = workspace_paths(root)
    (source / "fixture.txt").write_text("synthetic-source\n", encoding="utf-8")
    (source / "visible-canary.txt").write_text("SYNTHETIC-POSITIVE-CONTROL\n", encoding="utf-8")
    for path in (source / "fixture.txt", source / "visible-canary.txt"):
        path.chmod(0o644)

    # canaries OUTSIDE the mounts: paths the worker must not be able to reach
    outside = root / "outside"
    outside.mkdir()
    (outside / "fake-gh-hosts.yml").write_text(CANARY_VALUE, encoding="utf-8")
    (outside / "fake-fixer-token").write_text(CANARY_VALUE, encoding="utf-8")
    (outside / "out-of-container-canary").write_text(CANARY_VALUE, encoding="utf-8")
    for path in outside.iterdir():
        path.chmod(0o600)

    broker_dir = root / "broker"
    broker_dir.mkdir()
    os.chmod(broker_dir, 0o755)
    return source, work, broker_dir


def start_broker(broker_dir: Path, ledger: Path, *, provider: str, model: str, url: str, key: str):
    upstream = OpenAICompatUpstream(url, key, required=False)
    broker = Broker(upstream, ledger_path=ledger)
    cap = broker.issue_capability(
        run_id=f"cp2-{uuid.uuid4().hex[:8]}",
        provider=provider,
        model=model,
        max_requests=2,
        max_prompt_chars=4000,
        max_output_tokens=256,
    )
    socket_path = broker_dir / "broker.sock"
    # mode 0o660 would need a shared group; 0o666 is the widest and is chosen here explicitly
    # because the worker is uid 65534 and the broker is uid 1001 (see the report).
    server = broker.serve_socket(socket_path, mode=0o666).start()
    return broker, server, cap, socket_path


def run_container(spec: ContainerSpec, name: str, timeout: int) -> dict:
    argv = spec.to_argv(container_name=name)
    started = time.time()
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return {
        "argv": argv,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "seconds": round(time.time() - started, 2),
    }


def run_container_async(spec: ContainerSpec, name: str):
    """Start the container and return a callable that waits for its result."""
    argv = spec.to_argv(container_name=name)
    started = time.time()
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def wait(*, timeout: int) -> dict:
        out, err = proc.communicate(timeout=timeout)
        return {
            "argv": argv,
            "returncode": proc.returncode,
            "stdout": out,
            "stderr": err,
            "seconds": round(time.time() - started, 2),
        }

    return wait


def container_absent(name: str) -> bool:
    out = subprocess.check_output(
        ["docker", "--context", "rootless", "ps", "-aq", "--filter", f"name=^{name}$"], text=True
    ).strip()
    return not out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=["standin", "hermes"], default="standin")
    parser.add_argument("--json", action="store_true", help="print only the receipt")
    parser.add_argument("--keep", action="store_true", help="keep the scratch tree for inspection")
    parser.add_argument("--provider", default=os.environ.get("CS_PROVIDER", "local"))
    parser.add_argument("--model", default=os.environ.get("CS_MODEL", "auto"))
    parser.add_argument("--upstream-url", default=os.environ.get("CS_UPSTREAM_URL", ""))
    parser.add_argument("--credential-file", default=os.environ.get("CS_CREDENTIAL_FILE", ""))
    parser.add_argument(
        "--fake-upstream",
        action="store_true",
        help="serve a loopback fake provider (default when no --upstream-url is given)",
    )
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--stop-broker-before-run",
        action="store_true",
        help="fail-closed proof: stop the broker, then run; the worker must get no completion",
    )
    parser.add_argument(
        "--stop-broker-mid-run",
        action="store_true",
        help="fail-closed proof: stop the broker while the worker is running",
    )
    args = parser.parse_args(argv)

    root = new_run_dir(REPO, prefix="b1-")
    name = f"cs-b1-{uuid.uuid4().hex[:10]}"
    ledger = root / "receipts.jsonl"
    receipt: dict[str, object] = {"scratch": str(root), "container": name, "agent": args.agent}

    server = None
    fake: FakeProvider | None = None
    try:
        source, work, broker_dir = build_tree(root)
        key = "sk-FAKE-b1-synthetic-key-000000"
        if args.upstream_url:
            if not args.credential_file:
                raise SystemExit("--upstream-url needs --credential-file")
            key = read_secret_file(args.credential_file)
            upstream_url = args.upstream_url
            receipt["upstream"] = {"kind": "real", "url": args.upstream_url}
        else:
            fake = FakeProvider().__enter__()
            upstream_url = fake.url
            receipt["upstream"] = {"kind": "fake-loopback", "url": upstream_url}

        broker, server, cap, socket_path = start_broker(
            broker_dir, ledger,
            provider=args.provider, model=args.model,
            url=upstream_url, key=key,
        )
        receipt["broker"] = {"socket": str(socket_path), "capability_public": cap.public()}

        # task, broker config and capability are files in the workspace; the worker has no env.
        (work / "task.json").write_text(
            json.dumps({"prompt": "Reply with the single word: pong", "max_tokens": 32}), encoding="utf-8"
        )
        (work / "broker.json").write_text(
            json.dumps(
                {"socket": "/run/cs/broker.sock", "provider": args.provider,
                 "model": args.model, "timeout": 120}
            ),
            encoding="utf-8",
        )
        (work / "capability").write_text(cap.token + "\n", encoding="utf-8")
        os.chmod(work / "capability", 0o444)
        os.chmod(work, 0o777)

        # Host facts for the canaries. A file, not env: the spec forbids env, and this way the
        # container has no host-derived environment at all.
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

        image = IMAGE_HERMES if args.agent == "hermes" else IMAGE_PYTHON
        app_dir = work / "app"
        app_dir.mkdir()
        (app_dir / "worker.py").write_text(
            (REPO / "code_shipper" / "contain" / "worker.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (app_dir / "canary.py").write_text(
            (REPO / "spikes" / "b1" / "canary.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        os.chmod(app_dir, 0o755)
        for staged in app_dir.iterdir():
            os.chmod(staged, 0o644)

        spec = ContainerSpec(
            image=image,
            source_dir=source,
            work_dir=work,
            broker_dir=broker_dir,
            app_dir=app_dir,
            entrypoint="/usr/local/bin/python3" if args.agent == "standin" else "/bin/sh",
            command=(
                ["-c", _standin_script()]
                if args.agent == "standin"
                else ["-c", _hermes_script()]
            ),
        )
        spec.validate()
        receipt["spec"] = spec.describe()
        receipt["image_id"] = image_id(image)

        if args.stop_broker_before_run:
            server.stop()
            server = None
            receipt["broker_stopped_before_run"] = True

        if args.stop_broker_mid_run:
            # Worker that waits before one broker call, so the broker can die in the gap.
            (work / "task.json").write_text(
                json.dumps(
                    {"prompt": "Reply with the single word: pong", "max_tokens": 32, "delay_s": 6}
                ),
                encoding="utf-8",
            )
            wait_for_run = run_container_async(spec, name)
            time.sleep(2.0)                 # worker is up, canaries done, not yet at the socket
            assert server is not None
            server.stop()
            server = None
            receipt["broker_stopped_mid_run"] = True
            run_result = wait_for_run(timeout=args.timeout)
            receipt["broker_receipts"] = []
        else:
            run_result = run_container(spec, name, args.timeout)

        run = run_result
        receipt["run"] = {
            "returncode": run["returncode"],
            "seconds": run["seconds"],
            "stdout_tail": run["stdout"][-4000:],
            "stderr_tail": run["stderr"][-2000:],
        }
        receipt["container_removed"] = container_absent(name)

        canary_file = work / "canary.json"
        receipt["canary"] = (
            json.loads(canary_file.read_text(encoding="utf-8")) if canary_file.exists() else None
        )
        result_file = work / "result.json"
        receipt["result"] = (
            json.loads(result_file.read_text(encoding="utf-8")) if result_file.exists() else None
        )

        # the workspace is shared, so the worker could have read the capability: record it, since
        # a capability is a per-run token that dies with the run, not a credential.
        receipt["capability_file_in_workspace"] = True

        receipts = (
            [json.loads(line) for line in ledger.read_text().splitlines()]
            if ledger.exists()
            else []
        )
        receipt["broker_receipts"] = receipts
        receipt["ledger_text_has_key"] = key != "sk-FAKE-b1-synthetic-key-000000" and key in (
            ledger.read_text() if ledger.exists() else ""
        )
        receipt["ledger_has_prompt_text"] = "pong" in (ledger.read_text() if ledger.exists() else "")
        if fake is not None:
            receipt["fake_provider"] = {
                "hits": len(fake.hits),
                "saw_authorization": bool(fake.hits and fake.hits[0]["authorization"]),
                "model_requested": fake.hits[0]["model"] if fake.hits else None,
            }
            receipt["broker_reachable_from_container"] = bool(receipt.get("result"))

        ok = _verdict(receipt)
        receipt["verdict"] = "PASS" if ok else "FAIL"
        out = root / "receipt.json"
        out.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        if args.json:
            print(json.dumps(receipt, indent=2))
        else:
            _print_summary(receipt)
        return 0 if ok else 1
    finally:
        if server is not None:
            server.stop()
        if not args.keep:
            discard(root, root.parent)
        else:
            print(f"kept: {root}", file=sys.stderr)
        if fake is not None:
            fake.__exit__(None, None, None)


def _standin_script() -> str:
    # Two different run names, deliberately:
    #   canary -> a non-main name, so its own `sys.exit(main())` guard does not fire and end the
    #             process before the worker runs; its main() is called explicitly instead.
    #   worker -> '__main__', because its entry point IS that guard.
    return (
        "import runpy,sys;"
        "canary=runpy.run_path('/app/canary.py',run_name='canary');"
        "rc=canary['main']();"
        "sys.exit(rc) if rc else runpy.run_path('/app/worker.py',run_name='__main__')"
    )


def _hermes_script() -> str:
    return "echo 'cp3: hermes entrypoint not wired yet' >&2; exit 99"


def _verdict(receipt: dict) -> bool:
    if receipt.get("broker_stopped_mid_run"):
        return _mid_run_stop_verdict(receipt)
    if receipt.get("broker_stopped_before_run"):
        return _fail_closed_verdict(receipt)

    canary = receipt.get("canary") or {}
    run = receipt.get("run") or {}
    if canary.get("failed") or not canary.get("checks"):
        return False
    if receipt.get("ledger_text_has_key") or receipt.get("ledger_has_prompt_text"):
        return False
    if not receipt.get("container_removed"):
        return False
    if run.get("returncode") != 0:
        return False

    result = receipt.get("result") or {}
    if not result.get("text"):
        return False  # no completion means the channel is unproven, whatever else passed

    upstream = receipt.get("upstream") or {}
    if upstream.get("kind") == "fake-loopback":
        provider = receipt.get("fake_provider") or {}
        # the stand-in must have been reached exactly once, with a bearer header, and the worker
        # must have got its text back through the broker.
        if provider.get("hits") != 1:
            return False
        if not provider.get("saw_authorization"):
            return False
    receipts = receipt.get("broker_receipts") or []
    return any(entry.get("outcome") == "confirmed" for entry in receipts)


def _fail_closed_verdict(receipt: dict) -> bool:
    """The broker is stopped: the run must fail, produce no completion, and touch no model.

    This is the half of the proof that matters most. A container that merely cannot reach GitHub is
    not the claim; the claim is that inference specifically is unavailable when the broker is.

    One canary is EXPECTED to fail here: "broker socket present". Its absence is the point, so it
    is excluded from the pass requirement by name rather than excused globally.
    """
    run = receipt.get("run") or {}
    if receipt.get("result"):
        return False  # a completion exists with the broker down: the boundary failed
    if run.get("returncode") == 0:
        return False  # the worker must report the failure
    if (receipt.get("fake_provider") or {}).get("hits"):
        return False  # the provider must never have been called

    canary = receipt.get("canary") or {}
    checks = canary.get("checks") or []
    if not checks:
        return False

    socket_check = [entry for entry in checks if entry["name"] == "broker socket present"]
    if not socket_check or socket_check[0]["ok"]:
        return False  # the socket was there: nothing was proven

    unexpected = [
        entry["name"] for entry in checks if not entry["ok"] and entry["name"] != "broker socket present"
    ]
    if unexpected:
        return False

    # The worker must not have completed. It exits non-zero either because the canary stopped the
    # run at the missing socket, or because it tried and reported the broker unreachable. Both are
    # a refusal; a completion is the only failure mode.
    stderr = str(run.get("stderr_tail") or "")
    return "broker unreachable" in stderr or receipt.get("result") is None


def _mid_run_stop_verdict(receipt: dict) -> bool:
    """The broker was alive when the run started and died before the worker's call.

    Stricter than the before-run case in one way and looser in another: every canary must have
    passed (the socket really was there), and the worker must have failed at the socket with no
    completion and no provider contact.
    """
    run = receipt.get("run") or {}
    if receipt.get("result"):
        return False
    if run.get("returncode") == 0:
        return False
    if (receipt.get("fake_provider") or {}).get("hits"):
        return False
    canary = receipt.get("canary") or {}
    if canary.get("failed") or not canary.get("checks"):
        return False
    return "broker unreachable" in str(run.get("stderr_tail") or "")


def _print_summary(receipt: dict) -> None:
    print(f"container       {receipt['container']}")
    print(f"image           {receipt.get('image_id')}")
    print(f"agent           {receipt['agent']}")
    run = receipt.get("run") or {}
    print(f"run rc          {run.get('returncode')} in {run.get('seconds')}s")
    canary = receipt.get("canary") or {}
    print(f"canary          {canary.get('passed', 0)} passed, {len(canary.get('failed') or [])} failed")
    for name in canary.get("failed") or []:
        print(f"  FAILED        {name}")
    result = receipt.get("result") or {}
    print(f"worker result   {json.dumps(result)[:200] if result else '(none)'}")
    print(f"container gone  {receipt.get('container_removed')}")
    print(f"VERDICT         {receipt.get('verdict')}")


if __name__ == "__main__":
    raise SystemExit(main())
