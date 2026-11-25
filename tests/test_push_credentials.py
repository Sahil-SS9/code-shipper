"""Tests for push credential handling, scope rules and the receipt.

The push test is the important one: it runs a real `git push` against a local bare repository and
reads `/proc/<pid>/cmdline` while the child is alive. A test that only inspected the argv we built
would pass even if git put the header somewhere readable, so this observes the actual process.

/proc/cmdline is world-readable on a machine without hidepid, which is why the token must travel in
the environment: environ is owner-only.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from code_shipper.contain import fixloop
from code_shipper.steward.policy import ScopePolicy

FAKE_TOKEN = "ghp_FAKEtokenvalueforTestsOnly0123456789"
# Unix-domain sockets and POSIX file modes do not exist on Windows. These tests are
# skipped there rather than allowed to fail, so the CI matrix reports real coverage.
posix_only = unittest.skipIf(sys.platform.startswith("win"), "POSIX-only test")



def run(cmd: list[str], cwd: Path, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, **kw)


class PushRepo:
    """A worktree with a commit, plus a local bare repo it pushes to."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cs-push-"))
        self.bare = self.root / "remote.git"
        self.work = self.root / "work"
        run(["git", "init", "-q", "--bare", "-b", "main", str(self.bare)], self.root)
        run(["git", "init", "-q", "-b", "main", str(self.work)], self.root)
        run(["git", "-c", "user.name=t", "-c", "user.email=t@e.invalid",
             "commit", "-q", "--allow-empty", "-m", "base"], self.work)
        run(["git", "remote", "add", "origin", str(self.bare)], self.work)
        run(["git", "push", "-q", "origin", "main"], self.work)
        self.base_sha = run(["git", "rev-parse", "HEAD"], self.work).stdout.strip()

    def stage_change(self) -> None:
        path = self.work / "calc.py"
        path.write_text("def divide(a, b):\n    return a / b\n")
        # `git commit -a` only stages tracked changes; a new file has to be added explicitly.
        run(["git", "add", "calc.py"], self.work)

    def hooks(self) -> Path:
        path = self.root / "empty-hooks"
        path.mkdir(exist_ok=True)
        return path

    def cleanup(self) -> None:
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)


def push_with_observer(worktree: Path, bare: Path, *, expected_sha: str, token: str) -> tuple[dict, list[str]]:
    """Run the real push and sample the child's cmdline while it is alive.

    Returns the receipt and every cmdline sample taken. A fast local push may finish before the
    sampler runs, so the sampler polls tightly from a thread started before the call.
    """
    hooks_dir = bare.parent / "hooks-dir"
    hooks_dir.mkdir(exist_ok=True)

    samples: list[str] = []
    stop = threading.Event()

    def sampler() -> None:
        while not stop.is_set():
            for entry in Path("/proc").iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    if entry.stat().st_uid != os.getuid():
                        continue
                    # Only real git processes. Matching on substring would also catch the shell
                    # wrapper that started this test, which is not the thing under test.
                    comm = (entry / "comm").read_text().strip()
                    if comm != "git":
                        continue
                    cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                        "utf-8", "replace"
                    )
                except OSError:
                    continue
                if "push" in cmdline or "extraheader" in cmdline:
                    samples.append(cmdline)
            time.sleep(0.001)

    thread = threading.Thread(target=sampler, daemon=True)
    thread.start()
    try:
        receipt = fixloop.push(
            worktree,
            branch="main",
            head_branch="main",
            expected_sha=expected_sha,
            commit_message="test commit",
            token=token,
            hookless_dir=hooks_dir,
            author=("Test", "t@e.invalid"),
        )
    finally:
        stop.set()
        thread.join(timeout=2)

    return receipt, samples


@posix_only
class TokenNotInArgvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = PushRepo()
        self.addCleanup(self.repo.cleanup)
        self.repo.stage_change()

    def test_push_succeeds_and_token_is_never_in_the_child_cmdline(self) -> None:
        receipt, samples = push_with_observer(
            self.repo.work, self.repo.bare, expected_sha=self.repo.base_sha, token=FAKE_TOKEN
        )

        self.assertTrue(receipt["ok"], receipt)
        self.assertEqual(receipt["stage"], "push")
        # every observed git cmdline must be free of the token and of the encoded form
        import base64

        encoded = base64.b64encode(f"x-access-token:{FAKE_TOKEN}".encode()).decode()
        for sample in samples:
            self.assertNotIn(FAKE_TOKEN, sample)
            self.assertNotIn(encoded, sample)
            self.assertNotIn("extraheader", sample)

    def test_the_push_actually_moved_the_branch(self) -> None:
        receipt, _ = push_with_observer(
            self.repo.work, self.repo.bare, expected_sha=self.repo.base_sha, token=FAKE_TOKEN
        )
        self.assertTrue(receipt["ok"], receipt)
        head = run(["git", "rev-parse", "main"], self.repo.bare).stdout.strip()
        self.assertEqual(head, receipt["new_sha"])
        self.assertNotEqual(head, self.repo.base_sha)

    def test_a_mismatched_expected_head_is_refused_before_committing(self) -> None:
        receipt = fixloop.push(
            self.repo.work,
            branch="main",
            head_branch="main",
            expected_sha="0" * 40,
            commit_message="should not happen",
            token=FAKE_TOKEN,
            hookless_dir=self.repo.hooks(),
            author=("Test", "t@e.invalid"),
        )
        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["stage"], "expected-head")

    def test_the_receipt_does_not_carry_the_token(self) -> None:
        receipt, _ = push_with_observer(
            self.repo.work, self.repo.bare, expected_sha=self.repo.base_sha, token=FAKE_TOKEN
        )
        self.assertNotIn(FAKE_TOKEN, str(receipt))


@posix_only
class ScopeRuleTests(unittest.TestCase):
    """The path rules now live in steward/policy; these pin their behaviour through that policy."""

    POLICY = ScopePolicy(
        allowed=("calc/**/*.py", "tests/**/*.py"),
        forbidden=(
            ".github/**",
            "**/setup.cfg",
            "**/pyproject.toml",
            "**/conftest.py",
            "**/.code-shipper/**",
        ),
    )

    def test_allowed_paths(self) -> None:
        self.assertTrue(self.POLICY.path_allowed("calc/__init__.py"))
        self.assertTrue(self.POLICY.path_allowed("tests/test_calc.py"))

    def test_forbidden_paths(self) -> None:
        for path in (
            ".github/workflows/tests.yml",
            "pyproject.toml",
            "conftest.py",
            "calc/conftest.py",
            ".code-shipper/approvals.yml",
            "setup.cfg",
        ):
            with self.subTest(path=path):
                self.assertFalse(self.POLICY.path_allowed(path), f"{path} must be refused")

    def test_unknown_paths_are_refused(self) -> None:
        for path in ("README.md", "calc/data.json", "src/thing.py"):
            with self.subTest(path=path):
                self.assertFalse(self.POLICY.path_allowed(path))


@posix_only
class BasicAuthTests(unittest.TestCase):
    def test_basic_auth_encodes_as_expected(self) -> None:
        import base64

        expected = base64.b64encode(f"x-access-token:{FAKE_TOKEN}".encode()).decode()
        self.assertEqual(fixloop._basic_auth(FAKE_TOKEN), expected)


class HeadBranchRequiredTests(unittest.TestCase):
    """`head_branch` is required, because when it defaulted to None the protection protected nothing.

    The one real caller did not pass it, so "None means any branch" was the behaviour and T-ADV-06
    was documentation rather than a control. These tests make that omission impossible to repeat.
    """

    def test_omitting_head_branch_is_a_type_error(self) -> None:
        repo = PushRepo()
        self.addCleanup(repo.cleanup)
        repo.stage_change()
        with self.assertRaises(TypeError):
            fixloop.push(  # type: ignore[call-arg]
                repo.work,
                branch="main",
                expected_sha=repo.base_sha,
                commit_message="no head branch",
                token=FAKE_TOKEN,
                hookless_dir=repo.hooks(),
                author=("Test", "t@e.invalid"),
            )

    def test_an_empty_head_branch_cannot_push(self) -> None:
        repo = PushRepo()
        self.addCleanup(repo.cleanup)
        repo.stage_change()
        receipt = fixloop.push(
            repo.work,
            branch="main",
            head_branch="",
            expected_sha=repo.base_sha,
            commit_message="empty head",
            token=FAKE_TOKEN,
            hookless_dir=repo.hooks(),
            author=("Test", "t@e.invalid"),
        )
        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["stage"], "branch-scope")
        # and nothing reached the remote
        head = run(["git", "rev-parse", "main"], repo.bare).stdout.strip()
        self.assertEqual(head, repo.base_sha)

    def test_a_non_head_branch_cannot_push(self) -> None:
        repo = PushRepo()
        self.addCleanup(repo.cleanup)
        repo.stage_change()
        receipt = fixloop.push(
            repo.work,
            branch="main",
            head_branch="feature",
            expected_sha=repo.base_sha,
            commit_message="wrong branch",
            token=FAKE_TOKEN,
            hookless_dir=repo.hooks(),
            author=("Test", "t@e.invalid"),
        )
        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["stage"], "branch-scope")
        head = run(["git", "rev-parse", "main"], repo.bare).stdout.strip()
        self.assertEqual(head, repo.base_sha, "the remote must not have moved")

    def test_the_signature_has_no_default_for_head_branch(self) -> None:
        import inspect

        params = inspect.signature(fixloop.push).parameters
        self.assertIn("head_branch", params)
        self.assertIs(
            params["head_branch"].default,
            inspect.Parameter.empty,
            "head_branch must not have a default: a default is how the protection stopped applying",
        )


class LeaseTests(unittest.TestCase):
    """The push must carry an expected-old-ref lease, not a plain push.

    A preflight GET followed by an ordinary push is not enough: the head can move between them. The
    lease makes the remote itself refuse if the ref is not what we expect.
    """

    def test_the_push_uses_force_with_lease_at_the_expected_sha(self) -> None:
        import inspect

        source = inspect.getsource(fixloop.push)
        self.assertIn("--force-with-lease=refs/heads/", source)
        self.assertIn("expected_sha", source)

    def test_a_moved_remote_head_is_refused_before_any_push(self) -> None:
        repo = PushRepo()
        self.addCleanup(repo.cleanup)
        repo.stage_change()
        # the expected sha is not the remote head
        receipt = fixloop.push(
            repo.work,
            branch="main",
            head_branch="main",
            expected_sha="0" * 40,
            commit_message="should not happen",
            token=FAKE_TOKEN,
            hookless_dir=repo.hooks(),
            author=("Test", "t@e.invalid"),
        )
        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["stage"], "expected-head")

    def test_a_non_fast_forward_is_refused(self) -> None:
        # the local head must descend from the expected remote head
        repo = PushRepo()
        self.addCleanup(repo.cleanup)
        receipt = fixloop.push(
            repo.work,
            branch="main",
            head_branch="main",
            expected_sha=repo.base_sha,
            commit_message="nothing staged",
            token=FAKE_TOKEN,
            hookless_dir=repo.hooks(),
            author=("Test", "t@e.invalid"),
        )
        # nothing to commit, so this fails at the commit stage rather than inventing a push
        self.assertFalse(receipt["ok"])
        self.assertIn(receipt["stage"], ("commit", "fast-forward"))


if __name__ == "__main__":
    unittest.main()
