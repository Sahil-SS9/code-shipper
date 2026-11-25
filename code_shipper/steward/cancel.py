"""Cancellation: stop a run and leave nothing half-done (T-ADV-14, T-SCN-04).

Three things have to be true when a run is cancelled, and each is a distinct mechanism:

1. **The harness process dies.** Revoking a capability is not enough. Measured in Phase 0: Hermes
   treats a refused model call as a transient outage and retries for minutes, so a broker that stops
   answering produces a worker that keeps running. The process group is killed, because a harness that
   spawned children would otherwise leave them.

2. **The wall clock is ours.** Hermes ignores its own run budget when the provider refuses, so our
   timer is the only authority. The budget's clock is checked between steps and the process is killed
   when it expires.

3. **No half-pushed state remains.** A push either completed or it did not; there is no "cancelled
   push" to reconcile. The danger is cancelling *between* the commit and the push, which leaves a
   local commit that a later resume might push with no human having seen it. So cancellation before
   the push discards the workspace, and the receipt records that nothing left the host.

The provider sees nothing after the cancel point because the capability is revoked **and** the process
is dead: there is no live client left to send a request.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class Cancelled(Exception):
    """The run was cancelled. Carries what was in flight so the receipt can say."""

    def __init__(self, reason: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


@dataclass
class CancelToken:
    """Shared cancellation state, checked between steps and by the watchdog.

    Deliberately not a capability: possession of this object lets you *stop* work, never start or
    authorise it.
    """

    _event: threading.Event = field(default_factory=threading.Event)
    reason: str = ""
    requested_at: float | None = None
    requested_by: str = ""
    processes: list[subprocess.Popen] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self, reason: str, *, by: str = "") -> None:
        """Request cancellation and kill anything already running.

        Killing immediately rather than at the next check is the point: a harness mid-request would
        otherwise continue until the next boundary, and the whole complaint about refusals is that
        Hermes keeps going.
        """
        with self._lock:
            if self._event.is_set():
                return
            self.reason = reason
            self.requested_at = time.time()
            self.requested_by = by
            self._event.set()
            processes = list(self.processes)
        for process in processes:
            kill_process_group(process)

    def check(self) -> None:
        """Raise if cancelled. Call between steps, before any write."""
        if self._event.is_set():
            raise Cancelled(self.reason, {"requested_by": self.requested_by})

    def register(self, process: subprocess.Popen) -> None:
        """Track a process so cancellation can kill it. Kills at once if already cancelled."""
        with self._lock:
            if self._event.is_set():
                kill_process_group(process)
                return
            self.processes.append(process)

    def release(self, process: subprocess.Popen) -> None:
        with self._lock:
            if process in self.processes:
                self.processes.remove(process)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cancelled": self.cancelled,
            "reason": self.reason,
            "requested_by": self.requested_by,
            "requested_at": self.requested_at,
        }


def kill_process_group(process: subprocess.Popen, *, grace: float = 3.0) -> dict[str, Any]:
    """Terminate a process and its children, then confirm it is gone.

    SIGTERM first so a harness can flush, then SIGKILL. The return value is evidence rather than a
    promise: whether it was running, whether each signal was delivered, and whether it exited.
    """
    result: dict[str, Any] = {"pid": process.pid, "was_running": process.poll() is None}
    if result["was_running"]:
        try:
            if hasattr(os, "killpg"):
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            else:  # pragma: no cover - Windows
                process.terminate()
        except (ProcessLookupError, PermissionError, OSError) as exc:
            result["sigterm_error"] = str(exc)[:120]

        try:
            process.wait(timeout=grace)
            result["exited_after"] = "sigterm"
        except subprocess.TimeoutExpired:
            try:
                if hasattr(os, "killpg"):
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                else:  # pragma: no cover - Windows
                    process.kill()
                process.wait(timeout=grace)
                result["exited_after"] = "sigkill"
            except (subprocess.TimeoutExpired, ProcessLookupError, OSError) as exc:
                result["exited_after"] = "still_running"
                result["sigkill_error"] = str(exc)[:120]
    else:
        result["exited_after"] = "already_exited"
    result["returncode"] = process.returncode
    return result


def run_with_cancel(
    argv: list[str],
    *,
    token: CancelToken,
    timeout_s: float,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture: bool = True,
) -> dict[str, Any]:
    """Run a subprocess under our own wall clock, killing it on expiry or cancellation.

    `start_new_session` puts the child in its own process group so a kill reaches everything it
    spawned. Without it, `killpg` would signal our own group.

    Output is drained by reader threads rather than read after exit: a harness that writes more than
    the pipe buffer while we are waiting for it to finish would block forever, and reading afterwards
    leaves the handles open. Both were visible as ResourceWarnings before this was fixed.
    """
    import threading

    started = time.monotonic()
    process = subprocess.Popen(
        argv,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        start_new_session=True,
    )
    token.register(process)
    outcome: dict[str, Any] = {"argv": argv[:2], "timed_out": False, "cancelled": False}

    buffers: dict[str, list[str]] = {"stdout": [], "stderr": []}

    def _drain(name: str, stream) -> None:
        try:
            for line in stream:
                buffers[name].append(line)
        except (ValueError, OSError):
            pass
        finally:
            try:
                stream.close()
            except (ValueError, OSError):
                pass

    readers = []
    if capture:
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            if stream is not None:
                thread = threading.Thread(target=_drain, args=(name, stream), daemon=True)
                thread.start()
                readers.append(thread)

    deadline = started + timeout_s
    try:
        while True:
            if token.cancelled:
                outcome["cancelled"] = True
                outcome["kill"] = kill_process_group(process)
                break
            if process.poll() is not None:
                break
            if time.monotonic() >= deadline:
                outcome["timed_out"] = True
                outcome["kill"] = kill_process_group(process)
                break
            time.sleep(0.05)
    finally:
        token.release(process)

    for thread in readers:
        thread.join(timeout=5)

    outcome.update(
        {
            "returncode": process.returncode,
            "seconds": round(time.monotonic() - started, 2),
            "stdout": "".join(buffers["stdout"]),
            "stderr": "".join(buffers["stderr"]),
        }
    )
    if token.cancelled:
        raise Cancelled(token.reason, outcome)
    return outcome
