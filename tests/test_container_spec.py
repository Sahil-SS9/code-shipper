"""Tests for the container runtime spec.

These assert the properties the containment proof depends on, so a later edit that quietly
loosens the boundary fails here instead of surviving into a "measured" claim. No Docker needed:
the spec is data.

Fixtures deliberately build their paths outside any credential-bearing tree, because
``ContainerSpec.validate`` refuses to mount one. The staging directory for a real run has the same
constraint; see ``spikes/b1/README.md``.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from code_shipper.contain.spec import (
    APP_MOUNT,
    BROKER_MOUNT,
    WORKER_UID,
    ContainerSpec,
    workspace_paths,
)
from code_shipper.contain.staging import new_run_dir

REPO = Path(__file__).resolve().parent.parent


class SpecFixture(unittest.TestCase):
    """A spec over a throwaway tree that validate() accepts.

    Staged under the repo's gitignored ``.staging`` rather than the ambient TMPDIR, because
    TMPDIR here points inside ``~/.hermes`` and ``validate()`` rightly refuses to mount that.
    """

    def setUp(self) -> None:
        self.root = new_run_dir(REPO, prefix="test-spec-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def spec(self, **over: object) -> ContainerSpec:
        base: dict[str, object] = {
            "image": "img@sha256:deadbeef",
            "source_dir": self.root / "source",
            "work_dir": self.root / "work",
            "broker_dir": self.root / "broker",
            "entrypoint": "/usr/local/bin/python3",
        }
        base.update(over)
        return ContainerSpec(**base)  # type: ignore[arg-type]


class BoundaryTests(SpecFixture):
    def test_network_is_none(self) -> None:
        argv = self.spec().to_argv(container_name="c")
        self.assertEqual(argv[argv.index("--network") + 1], "none")

    def test_root_filesystem_is_read_only(self) -> None:
        self.assertIn("--read-only", self.spec().to_argv(container_name="c"))

    def test_runs_as_nobody(self) -> None:
        argv = self.spec().to_argv(container_name="c")
        self.assertEqual(argv[argv.index("--user") + 1], WORKER_UID)

    def test_all_capabilities_dropped(self) -> None:
        argv = self.spec().to_argv(container_name="c")
        self.assertEqual(argv[argv.index("--cap-drop") + 1], "ALL")

    def test_no_new_privileges(self) -> None:
        argv = self.spec().to_argv(container_name="c")
        self.assertEqual(argv[argv.index("--security-opt") + 1], "no-new-privileges")

    def test_no_privileged_or_host_network_flags(self) -> None:
        argv = self.spec().to_argv(container_name="c")
        self.assertNotIn("--privileged", argv)
        self.assertNotIn("--network=host", argv)

    def test_no_credential_store_or_docker_socket_mount(self) -> None:
        joined = " ".join(self.spec().to_argv(container_name="c"))
        for forbidden in ("docker.sock", "/var/run", "dbus", ".ssh", ".hermes", ".config"):
            self.assertNotIn(forbidden, joined, f"{forbidden} must not be reachable")

    def test_mounts_are_exactly_the_expected_destinations(self) -> None:
        argv = self.spec().to_argv(container_name="c")
        destinations = {
            part.split("dst=")[1].split(",")[0]
            for part in argv
            if part.startswith("type=bind")
        }
        self.assertEqual(destinations, {"/source", "/work", BROKER_MOUNT})

    def test_pull_is_never(self) -> None:
        argv = self.spec().to_argv(container_name="c")
        self.assertEqual(argv[argv.index("--pull") + 1], "never")

    def test_tmp_is_noexec(self) -> None:
        argv = self.spec().to_argv(container_name="c")
        tmpfs = argv[argv.index("--tmpfs") + 1]
        self.assertIn("noexec", tmpfs)
        self.assertIn("nosuid", tmpfs)

    def test_resource_limits_present(self) -> None:
        argv = self.spec().to_argv(container_name="c")
        self.assertIn("--pids-limit", argv)
        self.assertIn("--memory", argv)
        self.assertIn("--cpus", argv)


class MountTests(SpecFixture):
    def test_source_is_read_only_and_work_is_writable(self) -> None:
        argv = self.spec().to_argv(container_name="c")
        mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]
        source = next(m for m in mounts if "dst=/source" in m)
        work = next(m for m in mounts if "dst=/work" in m)
        self.assertIn("readonly", source)
        self.assertNotIn("readonly", work)

    def test_broker_directory_is_read_only(self) -> None:
        argv = self.spec().to_argv(container_name="c")
        mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]
        broker = next(m for m in mounts if f"dst={BROKER_MOUNT}" in m)
        self.assertIn("readonly", broker)

    def test_app_directory_is_optional_and_read_only(self) -> None:
        without = " ".join(self.spec().to_argv(container_name="c"))
        self.assertNotIn(f"dst={APP_MOUNT}", without)

        argv = self.spec(app_dir=self.root / "app").to_argv(container_name="c")
        mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]
        app = next(m for m in mounts if f"dst={APP_MOUNT}" in m)
        self.assertIn("readonly", app)


class ValidationTests(SpecFixture):
    def test_rejects_environment_variables(self) -> None:
        with self.assertRaises(ValueError) as caught:
            self.spec(env={"GH_TOKEN": "x"}).validate()
        self.assertIn("env must be empty", str(caught.exception))

    def test_rejects_a_credential_looking_mount(self) -> None:
        for path in ("/home/kensei/.hermes/x", "/home/u/.ssh/keys", "/home/u/.config/gh"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    self.spec(source_dir=Path(path)).validate()

    def test_rejects_relative_mounts(self) -> None:
        with self.assertRaises(ValueError):
            self.spec(source_dir=Path("relative/source")).validate()

    def test_rejects_a_relative_entrypoint(self) -> None:
        with self.assertRaises(ValueError):
            self.spec(entrypoint="python3").validate()

    def test_accepts_a_valid_spec(self) -> None:
        self.spec().validate()  # must not raise


class DescriptionTests(SpecFixture):
    def test_describe_reports_the_policy_as_data(self) -> None:
        described = self.spec().describe()
        self.assertEqual(described["network"], "none")
        self.assertEqual(described["root_filesystem"], "read-only")
        self.assertEqual(described["user"], WORKER_UID)
        self.assertIsNone(described["host_home"])
        self.assertIsNone(described["docker_socket"])
        mounts = described["mounts"]
        assert isinstance(mounts, dict)
        self.assertEqual(mounts["/source"]["mode"], "ro")
        self.assertEqual(mounts["/work"]["mode"], "rw")

    def test_argv_is_deterministic_for_the_same_spec(self) -> None:
        instance = self.spec(command=["-c", "echo hi"])
        self.assertEqual(
            instance.to_argv(container_name="c"),
            instance.to_argv(container_name="c"),
        )


class WorkspaceTests(unittest.TestCase):
    def test_workspace_paths_are_created_and_workspace_is_writable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cs-ws-") as tmp:
            source, work = workspace_paths(Path(tmp))
            self.assertTrue(source.is_dir())
            self.assertTrue(work.is_dir())
            self.assertTrue(
                (work.stat().st_mode & 0o777) & 0o002,
                "worker (uid 65534) must be able to write",
            )


if __name__ == "__main__":
    unittest.main()
