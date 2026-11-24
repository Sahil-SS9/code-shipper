"""Tests for the run-directory permission guard.

This guard is the compensating control for the 0o666 broker socket: another host user cannot reach
the socket because it cannot traverse the 0700 run directory that contains it. If this guard
silently passes on a widened directory, the justification for the socket mode disappears, so the
tests here check the failure direction specifically.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import sys
import unittest
from pathlib import Path

from code_shipper.contain.staging import (
    REQUIRED_MODE,
    STAGING_ENV,
    StagingPermissionError,
    discard,
    new_run_dir,
    require_private_run_dir,
    staging_root,
)

REPO = Path(__file__).resolve().parent.parent
# Unix-domain sockets and POSIX file modes do not exist on Windows. These tests are
# skipped there rather than allowed to fail, so the CI matrix reports real coverage.
posix_only = unittest.skipIf(sys.platform.startswith("win"), "POSIX-only test")



@posix_only
class RunDirTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = new_run_dir(REPO, prefix="test-perm-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_new_run_dir_is_0700(self) -> None:
        mode = stat.S_IMODE(os.stat(self.root).st_mode)
        self.assertEqual(mode, REQUIRED_MODE)
        self.assertEqual(mode, 0o700)

    def test_new_run_dir_is_owned_by_this_user(self) -> None:
        self.assertEqual(os.stat(self.root).st_uid, os.getuid())

    def test_guard_accepts_a_private_directory(self) -> None:
        require_private_run_dir(self.root)  # must not raise

    def test_guard_rejects_world_readable(self) -> None:
        os.chmod(self.root, 0o744)
        with self.assertRaises(StagingPermissionError) as caught:
            require_private_run_dir(self.root)
        self.assertIn("another host user could reach", str(caught.exception))

    def test_guard_rejects_group_readable(self) -> None:
        os.chmod(self.root, 0o750)
        with self.assertRaises(StagingPermissionError):
            require_private_run_dir(self.root)

    def test_guard_rejects_world_writable(self) -> None:
        os.chmod(self.root, 0o777)
        with self.assertRaises(StagingPermissionError):
            require_private_run_dir(self.root)

    def test_guard_rejects_sticky_or_setgid_bits(self) -> None:
        for extra in (stat.S_ISVTX, stat.S_ISGID, stat.S_ISUID):
            with self.subTest(bits=oct(extra)):
                os.chmod(self.root, REQUIRED_MODE | extra)
                with self.assertRaises(StagingPermissionError):
                    require_private_run_dir(self.root)
                os.chmod(self.root, REQUIRED_MODE)

    def test_guard_rejects_a_missing_directory(self) -> None:
        with self.assertRaises(StagingPermissionError):
            require_private_run_dir(self.root / "gone")

    def test_guard_rejects_a_regular_file(self) -> None:
        path = self.root / "file"
        path.write_text("x")
        with self.assertRaises(StagingPermissionError):
            require_private_run_dir(path)

    def test_widening_after_creation_is_caught_by_the_second_check(self) -> None:
        """The harness checks twice on purpose; this is what the second check is for."""
        require_private_run_dir(self.root)      # passes at creation
        os.chmod(self.root, 0o755)              # widened afterwards
        with self.assertRaises(StagingPermissionError):
            require_private_run_dir(self.root)  # caught before the broker starts


@posix_only
class StagingRootTests(unittest.TestCase):
    """CS_STAGING_ROOT may move the parent, but cannot weaken the per-run guarantee."""

    def setUp(self) -> None:
        self.parent = Path(tempfile.mkdtemp(prefix="test-staging-"))
        self.addCleanup(shutil.rmtree, self.parent, True)
        self._saved = os.environ.get(STAGING_ENV)

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop(STAGING_ENV, None)
        else:
            os.environ[STAGING_ENV] = self._saved

    def test_env_var_moves_the_parent(self) -> None:
        custom = self.parent / "custom"
        os.environ[STAGING_ENV] = str(custom)
        self.assertEqual(staging_root(REPO), custom)
        self.assertTrue(custom.is_dir())

    def test_env_var_does_not_weaken_the_run_directory(self) -> None:
        os.environ[STAGING_ENV] = str(self.parent)
        path = new_run_dir(REPO, prefix="env-perm-")
        self.addCleanup(shutil.rmtree, path, True)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o700)
        require_private_run_dir(path)

    def test_world_writable_parent_is_refused(self) -> None:
        open_parent = self.parent / "open"
        open_parent.mkdir()
        os.chmod(open_parent, 0o777)
        os.environ[STAGING_ENV] = str(open_parent)
        with self.assertRaises(StagingPermissionError) as caught:
            new_run_dir(REPO)
        self.assertIn("group/world writable", str(caught.exception))

    def test_group_writable_parent_is_refused(self) -> None:
        shared_parent = self.parent / "shared"
        shared_parent.mkdir()
        os.chmod(shared_parent, 0o770)
        os.environ[STAGING_ENV] = str(shared_parent)
        with self.assertRaises(StagingPermissionError):
            new_run_dir(REPO)

    def test_private_parent_is_accepted(self) -> None:
        private = self.parent / "private"
        private.mkdir()
        os.chmod(private, 0o700)
        os.environ[STAGING_ENV] = str(private)
        path = new_run_dir(REPO, prefix="priv-")
        self.addCleanup(shutil.rmtree, path, True)
        self.assertTrue(path.is_dir())


@posix_only
class DiscardTests(unittest.TestCase):
    def test_refuses_to_remove_outside_the_root(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="test-root-"))
        self.addCleanup(shutil.rmtree, root, True)
        inside = root / "run"
        inside.mkdir()
        outside = Path(tempfile.mkdtemp(prefix="test-outside-"))
        self.addCleanup(shutil.rmtree, outside, True)

        with self.assertRaises(ValueError):
            discard(outside, root)
        self.assertTrue(outside.exists())

    def test_refuses_to_remove_the_root_itself(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="test-root2-"))
        self.addCleanup(shutil.rmtree, root, True)
        with self.assertRaises(ValueError):
            discard(root, root)

    def test_removes_a_directory_inside_the_root(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="test-root3-"))
        self.addCleanup(shutil.rmtree, root, True)
        inside = root / "run"
        inside.mkdir()
        discard(inside, root)
        self.assertFalse(inside.exists())


class WindowsCapabilityTests(unittest.TestCase):
    """The privacy control cannot be evaluated on Windows, and must say so rather than pass.

    A directory reports mode 0777 there regardless of its ACL, so a mode check would silently accept
    anything. Refusing with an explanation is the honest behaviour, and it is what makes contained
    runs UNSUPPORTED on that platform rather than quietly unprotected.
    """

    def test_windows_refuses_rather_than_reporting_a_misleading_mode(self) -> None:
        from unittest import mock

        from code_shipper.contain import staging

        with mock.patch.object(staging.os, "name", "nt"):
            with self.assertRaises(staging.StagingPermissionError) as caught:
                staging.require_private_run_dir(Path("/anywhere"))
        message = str(caught.exception)
        self.assertIn("Windows", message)
        self.assertIn("UNSUPPORTED", message)

    def test_posix_still_enforces_the_mode(self) -> None:
        import os
        import tempfile

        from code_shipper.contain import staging

        if os.name == "nt":
            self.skipTest("POSIX-only assertion")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run"
            path.mkdir()
            os.chmod(path, 0o755)
            with self.assertRaises(staging.StagingPermissionError):
                staging.require_private_run_dir(path)


if __name__ == "__main__":
    unittest.main()
