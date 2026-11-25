"""The worker the watch loop drives: Hermes in the container, through the broker.

This is the production adapter, as distinct from the spike that ran once by hand. It holds no
credential — the broker does — and it consults the cancel token before and after the container run,
because the requirement is that cancellation kills the harness rather than merely stopping the caller
from looking at it.
"""

from __future__ import annotations

from pathlib import Path

from ..budget import RunBudget
from ..contain.runner import run_in_container
from .cancel import Cancelled, CancelToken
from .workspace import Workspace


class ContainedWorker:
    """Runs one fix inside a rootless container with no network."""

    def __init__(
        self,
        *,
        image: str = "hermes-agent-harness:latest",
        cancel: CancelToken | None = None,
        repo_root: Path | None = None,
        timeout_s: int = 600,
        max_requests: int = 24,
        upstream_url: str = "http://127.0.0.1:8091",
        host_credential: str = "synthetic-not-a-real-key",
    ) -> None:
        self.image = image
        self.cancel = cancel or CancelToken()
        self.repo_root = repo_root
        self.timeout_s = timeout_s
        self.max_requests = max_requests
        self.upstream_url = upstream_url
        self.host_credential = host_credential
        self.last_run: dict = {}

    def run(self, *, workspace: Workspace, item, prompt: str, budget: RunBudget) -> dict:
        # Before: a cancelled run must not start a container at all.
        self.cancel.check()

        budget.check_clock()
        try:
            result = run_in_container(
                image=self.image,
                work_dir=workspace.work,
                source_dir=workspace.pristine,
                broker_dir=workspace.broker_dir,
                prompt=prompt,
                timeout_s=self.timeout_s,
                max_requests=self.max_requests,
                upstream_url=self.upstream_url,
                host_credential=self.host_credential,
                repo_root=self.repo_root,
                keep=False,
                cancel=self.cancel,
            )
        except Cancelled as exc:
            # Keep the container's own evidence — how long it was alive, which signal killed it —
            # rather than replacing it with a summary. That detail is the proof.
            self.last_run = {
                "cancelled": True,
                "reason": self.cancel.reason,
                "cancelled_at": self.cancel.requested_at,
                "cancelled_by": self.cancel.requested_by,
                "container_seconds_alive": (exc.detail or {}).get("container_seconds_alive"),
                "kill": (exc.detail or {}).get("kill"),
                "container": (exc.detail or {}).get("container"),
            }
            raise Cancelled(exc.reason, self.last_run) from None
        self.last_run = result.as_dict()

        # After: the container has exited, but a cancel that arrived mid-run means this work does not
        # count. Raising here is what stops the push in run_fix.
        if self.cancel.cancelled:
            raise Cancelled(
                self.cancel.reason or "cancelled during the container run",
                {"container": self.last_run},
            )

        budget.record_cost(None)  # local loopback model: cost unknown, recorded as unknown
        return {
            "changed": result.changed,
            "removed": result.removed,
            "returncode": result.returncode,
            "ok": result.ok,
            "adapter_calls": result.adapter_calls,
            "canary": result.canary,
        }
