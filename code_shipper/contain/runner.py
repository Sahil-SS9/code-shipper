"""Run the fix worker inside the container, through the broker.

This is the host side of the contained run, factored out of the CP4 spike so the spike and the real
steward path use the same code. Duplicating it would mean the thing that was proven and the thing
that ships could drift apart.

The shape, in order:

1. A private run directory (0700) holding `/work`, the app bundle, and the broker socket directory.
2. A broker on the host with the only credential, issuing one bounded capability for this run.
3. `docker run` with `--network none`, read-only `/source`, read-write `/work`, and the socket
   mounted read-only. The container has no route anywhere and no credential of its own.
4. A canary first, then the fix worker, in one container, so containment is checked before any fix.
5. On return, the broker is stopped and the capability is dead.

The credential never enters the container. What it gets is a capability token scoped to one provider,
one model, a request count and an expiry.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..steward.cancel import CancelToken, Cancelled, run_with_cancel
from .spec import APP_MOUNT, ContainerSpec

DOCKER_CONTEXT = "rootless"
BROKER_SOCKET_MODE = 0o666  # safe only because the run directory is 0700; enforced below


@dataclass
class ContainerRun:
    """Everything the host observed about one contained run."""

    ok: bool = False
    returncode: int | None = None
    seconds: float = 0.0
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    adapter_calls: int = 0
    adapter_refused: int = 0
    canary: dict = field(default_factory=dict)
    stdout_tail: str = ""
    stderr_tail: str = ""
    hermes: dict = field(default_factory=dict)
    image_id: str = ""
    run_dir_mode: str = ""
    ledger_lines_at_start: int = 0
    ledger_lines_at_end: int = 0

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "returncode": self.returncode,
            "seconds": self.seconds,
            "changed": self.changed,
            "removed": self.removed,
            "adapter_calls": self.adapter_calls,
            "adapter_refused": self.adapter_refused,
            "canary": self.canary,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
            "hermes": self.hermes,
            "image_id": self.image_id,
            "run_dir_mode": self.run_dir_mode,
            "ledger_lines_at_start": self.ledger_lines_at_start,
            "ledger_lines_at_end": self.ledger_lines_at_end,
        }


def _docker(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "--context", DOCKER_CONTEXT, *args], capture_output=True, text=True, check=check
    )


def image_id(image: str) -> str:
    return _docker("image", "inspect", image, "--format", "{{.Id}}").stdout.strip()


def run_in_container(
    *,
    image: str,
    work_dir: Path,
    source_dir: Path,
    broker_dir: Path,
    prompt: str,
    timeout_s: int = 600,
    max_requests: int = 24,
    upstream_url: str = "http://127.0.0.1:8091",
    host_credential: str = "synthetic-not-a-real-key",
    repo_root: Path | None = None,
    keep: bool = False,
    cancel: CancelToken | None = None,
) -> ContainerRun:
    """One contained fix run. Returns what happened; never raises for a worker failure."""
    from ..inference.broker import Broker
    from ..inference.upstream import OpenAICompatUpstream

    repo_root = repo_root or Path(__file__).resolve().parents[2]
    cancel = cancel or CancelToken()
    root = work_dir.parent
    app_dir = root / "app"
    app_dir.mkdir(exist_ok=True)

    # The app bundle: no credentials, no host paths. Only what the worker needs.
    for name, source in (
        ("canary.py", repo_root / "spikes" / "b1" / "canary.py"),
        ("adapter.py", repo_root / "code_shipper" / "contain" / "adapter.py"),
        ("fix_worker.py", repo_root / "code_shipper" / "contain" / "fix_worker.py"),
    ):
        (app_dir / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    app_dir.chmod(0o755)
    for staged in app_dir.iterdir():
        staged.chmod(0o644)

    # The canary's positive controls: a file it must be able to read through the read-only mount,
    # so "absent" and "the mount is broken" are distinguishable. Synthetic content only.
    (source_dir / "fixture.txt").write_text("synthetic-source\n", encoding="utf-8")
    (source_dir / "visible-canary.txt").write_text("SYNTHETIC-POSITIVE-CONTROL\n", encoding="utf-8")
    for fixture in ("fixture.txt", "visible-canary.txt"):
        (source_dir / fixture).chmod(0o644)

    (work_dir / "task.json").write_text(
        json.dumps({"failure": prompt[-4000:], "timeout_s": timeout_s}), encoding="utf-8"
    )
    (work_dir / "broker.json").write_text(
        json.dumps({"socket": "/run/cs/broker.sock", "provider": "local", "model": "auto",
                    "timeout": 180}),
        encoding="utf-8",
    )

    broker = Broker(
        OpenAICompatUpstream(upstream_url, host_credential),
        ledger_path=root / "ledger.jsonl",
    )
    capability = broker.issue_capability(
        run_id=f"steward-{uuid.uuid4().hex[:8]}",
        provider="local",
        model="auto",
        max_requests=max_requests,
        max_prompt_chars=120_000,
        max_output_tokens=2048,
    )
    (work_dir / "capability").write_text(capability.token + "\n", encoding="utf-8")
    (work_dir / "capability").chmod(0o444)

    # The canary compares against the host's real values, so it needs them. These are identifiers,
    # not credentials: a pid, a namespace link, and paths that must NOT be reachable from inside.
    outside = root / "outside"
    outside.mkdir(exist_ok=True)
    for name in ("out-of-container-canary", "fake-gh-hosts.yml", "fake-fixer-token"):
        (outside / name).write_text("FAKE-CREDENTIAL-MUST-NOT-BE-REACHABLE")
        (outside / name).chmod(0o600)
    (work_dir / "host-probe.json").write_text(
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
    (work_dir / "host-probe.json").chmod(0o444)
    work_dir.chmod(0o777)

    server = broker.serve_socket(broker_dir / "broker.sock", mode=BROKER_SOCKET_MODE).start()

    spec = ContainerSpec(
        image=image,
        source_dir=source_dir,
        work_dir=work_dir,
        broker_dir=broker_dir,
        app_dir=app_dir,
        entrypoint="/opt/hermes/.venv/bin/python3",
    )
    spec.validate()

    name = f"cs-steward-{uuid.uuid4().hex[:10]}"
    script = (
        "import runpy,sys;"
        "canary=runpy.run_path('/app/canary.py',run_name='canary');"
        "rc=canary['main']();"
        "sys.exit(rc) if rc else runpy.run_path('/app/fix_worker.py',run_name='__main__')"
    )
    result = ContainerRun(image_id=image_id(image))
    result.run_dir_mode = oct(os.stat(root).st_mode & 0o777)

    # How many model calls had the broker served when the run ended? Comparing this to the ceiling
    # is what shows a cancel landed mid-work rather than after the harness had finished.
    def _ledger_calls() -> int:
        path = root / "ledger.jsonl"
        try:
            return len([line for line in path.read_text().splitlines() if line.strip()])
        except OSError:
            return 0

    result.ledger_lines_at_start = _ledger_calls()

    started = time.time()
    try:
        # Run under our own cancel token rather than subprocess.run: a container that ignores its
        # timeout and keeps working has to be killable mid-request, and `subprocess.run` can only
        # wait. Without this, a cancel during a fix was noticed only after the container finished.
        argv = spec.to_argv(container_name=name) + ["-c", script]
        try:
            proc_outcome = run_with_cancel(
                argv, token=cancel, timeout_s=timeout_s + 180
            )
            result.returncode = proc_outcome["returncode"]
            result.stdout_tail = proc_outcome["stdout"][-3000:]
            result.stderr_tail = proc_outcome["stderr"][-2000:]
            if proc_outcome.get("cancelled"):
                result.stderr_tail = (
                    "cancelled and the container was killed mid-run; "
                    + result.stderr_tail[-1500:]
                )
            elif proc_outcome.get("timed_out"):
                result.stderr_tail = (
                    f"container exceeded {timeout_s + 180}s and was killed; "
                    + result.stderr_tail[-1500:]
                )
        except Cancelled as exc:
            result.returncode = -9
            result.seconds = round(time.time() - started, 1)
            result.stderr_tail = "cancelled: the container was killed before it finished"
            # Carry the kill evidence up: when it was issued, how long the container had been alive,
            # and which signal it took. That is what distinguishes "killed mid-work" from "noticed
            # after it had already finished".
            detail = dict(exc.detail or {})
            detail["container_seconds_alive"] = result.seconds
            detail["container"] = result.as_dict()
            raise Cancelled(exc.reason, detail) from None
        finally:
            result.seconds = round(time.time() - started, 1)
            result.ledger_lines_at_end = _ledger_calls()

        canary_file = work_dir / "canary.json"
        if canary_file.exists():
            result.canary = json.loads(canary_file.read_text(encoding="utf-8"))

        fix_file = work_dir / "fix-result.json"
        if fix_file.exists():
            payload = json.loads(fix_file.read_text(encoding="utf-8"))
            result.changed = payload.get("changed", [])
            result.removed = payload.get("removed", [])
            result.adapter_calls = payload.get("adapter_calls", 0)
            result.adapter_refused = payload.get("adapter_refused", 0)
            result.hermes = payload.get("hermes", {})

        result.ok = result.returncode == 0 and bool(result.changed)
    except Cancelled:
        # The kill already happened inside run_with_cancel; the container must not survive it.
        _docker("rm", "-f", name)
        raise
    finally:
        server.stop()
        _docker("rm", "-f", name)
        # The run root belongs to the caller (the steward's workspace), so its removal is not this
        # function's decision. Only the scratch this function created is cleaned here.
        if not keep:
            shutil.rmtree(root / "app", ignore_errors=True)
            shutil.rmtree(root / "outside", ignore_errors=True)

    return result
