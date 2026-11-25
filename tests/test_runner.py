"""Tests for the container runner's host-side logic.

The container itself is exercised by the real run (see the P3B receipt); these tests cover the parts
that can be checked without Docker: the spec it builds, the refusal paths, and the guarantee that no
credential reaches the container.

Anything requiring Docker is skipped when the daemon is not reachable, so the CI matrix stays honest
rather than reporting a false failure.
"""

from __future__ import annotations

import json
import os
import sys
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from code_shipper.contain import runner
from code_shipper.contain.spec import APP_MOUNT, BROKER_MOUNT, ContainerSpec


def docker_available() -> bool:
    try:
        proc = subprocess.run(
            ["docker", "--context", runner.DOCKER_CONTEXT, "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=20,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


DOCKER = docker_available()


class SpecTests(unittest.TestCase):
    """The spec is the boundary, so its shape is asserted rather than assumed."""

    def _spec(self) -> ContainerSpec:
        # Not the system temp dir: on this rig TMPDIR points inside ~/.hermes, and the spec correctly
        # refuses to mount anything credential-looking. The repo's .staging is where real runs live.
        staging = Path(__file__).resolve().parent.parent / ".staging"
        staging.mkdir(exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="cs-spec-", dir=staging))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        work = root / "work"
        source = root / "source"
        broker = root / "broker"
        app = root / "app"
        for path in (work, source, broker, app):
            path.mkdir()
        return ContainerSpec(
            image="hermes-agent-harness:latest",
            source_dir=source,
            work_dir=work,
            broker_dir=broker,
            app_dir=app,
            entrypoint="/opt/hermes/.venv/bin/python3",
        )

    def test_the_container_has_no_network(self) -> None:
        argv = self._spec().to_argv(container_name="t")
        self.assertIn("--network", argv)
        self.assertEqual(argv[argv.index("--network") + 1], "none")

    def test_the_source_is_read_only(self) -> None:
        spec = self._spec()
        argv = spec.to_argv(container_name="t")
        # --mount with a readonly flag, not the short -v form
        mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]
        source_mount = next(m for m in mounts if "dst=/source" in m)
        self.assertIn("readonly", source_mount)
        self.assertIn(str(spec.source_dir), source_mount)

    def test_no_credential_path_is_mounted(self) -> None:
        describes = json.dumps(self._spec().describe()).lower()
        for forbidden in ("token", "credential", "secret", ".ssh", ".config"):
            self.assertNotIn(forbidden, describes)

    def test_the_worker_runs_as_nobody(self) -> None:
        argv = self._spec().to_argv(container_name="t")
        self.assertIn("--user", argv)
        self.assertEqual(argv[argv.index("--user") + 1], "65534:65534")

    def test_a_host_path_under_home_is_refused(self) -> None:
        spec = self._spec()
        widened = ContainerSpec(
            image=spec.image,
            source_dir=Path.home() / ".hermes",
            work_dir=spec.work_dir,
            broker_dir=spec.broker_dir,
            app_dir=spec.app_dir,
            entrypoint=spec.entrypoint,
        )
        with self.assertRaises(Exception):
            widened.validate()

    @unittest.skipUnless(not sys.platform.startswith("win"), "POSIX mode bits only")
    def test_a_world_writable_work_directory_is_allowed_but_the_run_root_is_not(self) -> None:
        # Two different rules, and conflating them would break every real run.
        #   /work            must be world-writable: the worker is a different uid and must edit it.
        #   the run directory must be 0700: it holds the broker socket and nothing else may reach it.
        from code_shipper.steward.workspace import WorkspaceError, require_private_dir

        spec = self._spec()
        os.chmod(spec.work_dir, 0o777)
        spec.validate()  # allowed, and must stay allowed

        run_root = spec.work_dir.parent
        os.chmod(run_root, 0o777)
        with self.assertRaises(WorkspaceError):
            require_private_dir(run_root)


class CapabilityTests(unittest.TestCase):
    """The container gets a bounded capability, never the credential."""

    def test_the_capability_is_written_but_the_credential_is_not(self) -> None:
        from code_shipper.inference.broker import Broker
        from code_shipper.inference.upstream import OpenAICompatUpstream

        root = Path(tempfile.mkdtemp(prefix="cs-cap-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        work = root / "work"
        work.mkdir()

        credential = "sk-REAL-LOOKING-CREDENTIAL-VALUE"
        broker = Broker(OpenAICompatUpstream("http://127.0.0.1:9", credential))
        capability = broker.issue_capability(
            run_id="t", provider="local", model="auto", max_requests=2
        )

        (work / "capability").write_text(capability.token + "\n")
        (work / "task.json").write_text(json.dumps({"failure": "synthetic"}))

        for path in work.iterdir():
            self.assertNotIn(credential, path.read_text(), f"{path.name} leaked the credential")
        self.assertNotEqual(capability.token, credential)

    def test_the_capability_carries_a_request_ceiling(self) -> None:
        from code_shipper.inference.broker import Broker
        from code_shipper.inference.upstream import OpenAICompatUpstream

        broker = Broker(OpenAICompatUpstream("http://127.0.0.1:9", "x"))
        capability = broker.issue_capability(
            run_id="t", provider="local", model="auto", max_requests=7
        )
        self.assertEqual(capability.max_requests, 7)


@unittest.skipUnless(DOCKER, "rootless docker is not reachable here")
class RealContainerTests(unittest.TestCase):
    """One small real run: canary only, no fix, to prove the path itself works."""

    def test_the_canary_passes_in_the_container(self) -> None:
        # Not under ~/.hermes: the spec refuses to mount anything credential-looking, and that
        # guard is right. The repo's own .staging is the real location a run uses.
        staging = Path(__file__).resolve().parent.parent / ".staging"
        staging.mkdir(exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="cs-runner-", dir=staging))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        work = root / "work"
        source = root / "source"
        broker = root / "broker"
        for path in (work, source, broker):
            path.mkdir()
        (source / "fixture.txt").write_text("synthetic\n")

        result = runner.run_in_container(
            image="hermes-agent-harness:latest",
            work_dir=work,
            source_dir=source,
            broker_dir=broker,
            prompt="reply with pong",
            timeout_s=90,
            max_requests=2,
            repo_root=Path(__file__).resolve().parent.parent,
        )
        # the canary is the real assertion: it ran, and it found nothing reachable
        self.assertTrue(result.canary, "the canary did not produce a result")
        self.assertEqual(result.canary.get("failed"), [], result.canary.get("failed"))
        self.assertGreater(result.canary.get("passed", 0), 0)


if __name__ == "__main__":
    unittest.main()
