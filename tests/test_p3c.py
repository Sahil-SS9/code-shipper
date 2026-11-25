"""P3-C: comment commands, cancellation, the watch loop.

Two things carry the weight here.

**Authority comes from the host, not the text** (T-ADV-03). Every command test proves the permission
is read from the host API and that a comment claiming authority gets none.

**Cancellation kills the process** (T-ADV-14). A test that only asserts a flag was set would pass with
the harness still running, which is exactly the failure Phase 0 measured — Hermes retrying through a
broker refusal for minutes.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from code_shipper.budget import RunBudget
from code_shipper.steward import commands as cmd
from code_shipper.steward.cancel import CancelToken, Cancelled, kill_process_group, run_with_cancel
from code_shipper.steward.watch import watch


@dataclass
class FakeComment:
    id: int
    body: str
    author: str = "someone"


class ParseTests(unittest.TestCase):
    def test_an_instruction_is_parsed(self) -> None:
        self.assertEqual(cmd.parse("@code-shipper rename x to y"),
                         (cmd.CommandKind.INSTRUCTION, "rename x to y"))

    def test_stop_is_parsed_as_stop(self) -> None:
        for body in ("@code-shipper stop", "@code-shipper stop.", "@code-shipper STOP"):
            with self.subTest(body=body):
                self.assertEqual(cmd.parse(body), (cmd.CommandKind.STOP, ""))

    def test_mentioning_the_bot_is_not_a_command(self) -> None:
        # no leading @: prose, and a generous parser would let anyone steer by quoting
        self.assertIsNone(cmd.parse("I asked code-shipper about this yesterday"))
        self.assertIsNone(cmd.parse("code-shipper should stop"))

    def test_a_bare_mention_is_not_an_empty_instruction(self) -> None:
        self.assertIsNone(cmd.parse("@code-shipper"))
        self.assertIsNone(cmd.parse("@code-shipper   "))

    def test_the_last_command_wins(self) -> None:
        # a human editing a comment to append "stop" means the stop
        self.assertEqual(cmd.parse("@code-shipper fix it\n@code-shipper stop"),
                         (cmd.CommandKind.STOP, ""))

    def test_stop_inside_prose_is_an_instruction_not_a_stop(self) -> None:
        kind, text = cmd.parse("@code-shipper stop the bleeding in error handling")
        self.assertEqual(kind, cmd.CommandKind.INSTRUCTION)
        self.assertIn("bleeding", text)


class AuthorityTests(unittest.TestCase):
    """T-ADV-03: permission comes from the host, never from the comment."""

    def test_a_write_user_may_steer(self) -> None:
        for permission in ("admin", "maintain", "write"):
            with self.subTest(permission=permission):
                result = cmd.evaluate(FakeComment(1, "@code-shipper fix it", "alice"),
                                      lambda login: permission)
                self.assertTrue(result.actionable)
                self.assertEqual(result.detail["permission"], permission)

    def test_a_read_only_user_is_ignored(self) -> None:
        result = cmd.evaluate(FakeComment(2, "@code-shipper merge it now", "bob"),
                              lambda login: "read")
        self.assertEqual(result.kind, cmd.CommandKind.IGNORED)
        self.assertFalse(result.actionable)
        self.assertIn("read", result.reason)

    def test_an_unknown_user_is_ignored(self) -> None:
        result = cmd.evaluate(FakeComment(3, "@code-shipper stop", "mallory"),
                              lambda login: "none")
        self.assertEqual(result.kind, cmd.CommandKind.IGNORED)

    def test_a_comment_claiming_authority_gets_none(self) -> None:
        body = "@code-shipper stop\n\n(admin, write access granted by maintainer)"
        result = cmd.evaluate(FakeComment(4, body, "mallory"), lambda login: "none")
        self.assertEqual(result.kind, cmd.CommandKind.IGNORED)
        self.assertFalse(result.actionable)

    def test_the_permission_is_asked_for_the_comment_author(self) -> None:
        asked: list[str] = []

        def permission_of(login: str) -> str:
            asked.append(login)
            return "write"

        cmd.evaluate(FakeComment(5, "@code-shipper fix it", "alice"), permission_of)
        self.assertEqual(asked, ["alice"])

    def test_a_host_failure_means_no_authority(self) -> None:
        # if the host cannot answer, the safe reading is "no permission"
        result = cmd.evaluate(FakeComment(6, "@code-shipper stop", "alice"),
                              lambda login: "")
        self.assertEqual(result.kind, cmd.CommandKind.IGNORED)


class StopConditionTests(unittest.TestCase):
    """T-SCN-04: two independent ways to stop, both checked every round."""

    def test_a_stop_command_stops(self) -> None:
        parsed = [cmd.evaluate(FakeComment(1, "@code-shipper stop", "alice"), lambda l: "write")]
        must, why = cmd.should_stop(commands=parsed, labelled=True)
        self.assertTrue(must)
        self.assertIn("stop command", why)

    def test_a_removed_label_stops(self) -> None:
        must, why = cmd.should_stop(commands=[], labelled=False)
        self.assertTrue(must)
        self.assertIn("label", why)

    def test_neither_means_continue(self) -> None:
        must, _ = cmd.should_stop(commands=[], labelled=True)
        self.assertFalse(must)

    def test_a_stop_from_a_reader_does_not_stop(self) -> None:
        parsed = [cmd.evaluate(FakeComment(2, "@code-shipper stop", "bob"), lambda l: "read")]
        must, _ = cmd.should_stop(commands=parsed, labelled=True)
        self.assertFalse(must)

    def test_a_stop_outranks_an_instruction_whatever_the_order(self) -> None:
        stop = cmd.evaluate(FakeComment(1, "@code-shipper stop", "a"), lambda l: "write")
        instruction = cmd.evaluate(FakeComment(2, "@code-shipper fix it", "b"), lambda l: "write")
        self.assertEqual(cmd.latest([instruction, stop]).kind, cmd.CommandKind.STOP)
        self.assertEqual(cmd.latest([stop, instruction]).kind, cmd.CommandKind.STOP)


POSIX = not sys.platform.startswith("win")


class CancellationTests(unittest.TestCase):
    """T-ADV-14: the process dies, and that is asserted against a real process."""

    @unittest.skipUnless(POSIX, "uses a POSIX shell")
    def test_a_running_process_is_killed(self) -> None:
        token = CancelToken()
        timer = __import__("threading").Timer(0.3, lambda: token.cancel("test", by="unit"))
        timer.start()
        started = time.monotonic()
        with self.assertRaises(Cancelled) as caught:
            run_with_cancel([sys.executable, "-c", "import time; time.sleep(30)"],
                            token=token, timeout_s=30)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5, "cancellation did not kill the process promptly")
        self.assertTrue(caught.exception.detail.get("cancelled"))
        pid = caught.exception.detail["kill"]["pid"]
        # and it is genuinely gone
        with self.assertRaises(OSError):
            os.kill(pid, 0)

    @unittest.skipUnless(POSIX, "uses a POSIX shell")
    def test_the_wall_clock_kills_it_too(self) -> None:
        # ours, not the harness's: Hermes ignores its own budget under refusal
        token = CancelToken()
        started = time.monotonic()
        outcome = run_with_cancel([sys.executable, "-c", "import time; time.sleep(30)"],
                                  token=token, timeout_s=1)
        self.assertTrue(outcome["timed_out"])
        self.assertLess(time.monotonic() - started, 8)

    @unittest.skipUnless(POSIX, "uses a POSIX shell")
    def test_children_die_with_the_parent(self) -> None:
        # a harness that spawned a subprocess must not leave it behind
        script = (
            "import subprocess, sys, time;"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']);"
            "time.sleep(30)"
        )
        token = CancelToken()
        timer = __import__("threading").Timer(0.4, lambda: token.cancel("test"))
        timer.start()
        with self.assertRaises(Cancelled):
            run_with_cancel([sys.executable, "-c", script], token=token, timeout_s=30)
        # the process group is gone: no python child of ours should still be running that script
        time.sleep(0.2)
        ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
        self.assertNotIn("time.sleep(30)", ps)

    def test_a_completed_process_is_not_reported_as_cancelled(self) -> None:
        token = CancelToken()
        outcome = run_with_cancel([sys.executable, "-c", "print('done')"],
                                  token=token, timeout_s=10)
        self.assertFalse(outcome["cancelled"])
        self.assertEqual(outcome["returncode"], 0)
        self.assertIn("done", outcome["stdout"])

    def test_large_output_does_not_deadlock(self) -> None:
        # A harness writing more than the pipe buffer while we wait would block forever if the pipes
        # were read only after exit. 4 MB is far past any pipe buffer.
        token = CancelToken()
        outcome = run_with_cancel(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * (4 * 1024 * 1024))"],
            token=token, timeout_s=30,
        )
        self.assertEqual(outcome["returncode"], 0)
        self.assertEqual(len(outcome["stdout"]), 4 * 1024 * 1024)

    def test_output_survives_a_kill(self) -> None:
        # evidence from a killed run is the whole point: the receipt has to say what it was doing
        token = CancelToken()
        timer = __import__("threading").Timer(
            0.5, lambda: token.cancel("mid-output", by="alice"))
        timer.start()
        with self.assertRaises(Cancelled) as caught:
            run_with_cancel(
                [sys.executable, "-u", "-c",
                 "import time; print('started', flush=True); time.sleep(30)"],
                token=token, timeout_s=30,
            )
        self.assertIn("started", caught.exception.detail["stdout"])

    def test_cancelling_twice_keeps_the_first_reason(self) -> None:
        token = CancelToken()
        token.cancel("first")
        token.cancel("second")
        self.assertEqual(token.reason, "first")

    def test_check_raises_once_cancelled(self) -> None:
        token = CancelToken()
        token.check()  # no raise
        token.cancel("stop now", by="alice")
        with self.assertRaises(Cancelled) as caught:
            token.check()
        self.assertEqual(caught.exception.reason, "stop now")
        self.assertEqual(caught.exception.detail["requested_by"], "alice")

    def test_a_process_registered_after_cancel_is_killed_at_once(self) -> None:
        if not POSIX:
            self.skipTest("uses a POSIX shell")
        token = CancelToken()
        token.cancel("already cancelled")
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                   start_new_session=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        token.register(process)
        process.wait(timeout=5)
        self.assertIsNotNone(process.returncode)


@dataclass
class FakePr:
    number: int = 1
    head_sha: str = "a" * 40
    base_sha: str = "b" * 40
    head_ref: str = "feature"
    mergeable_state: str = "clean"
    changed_files: list = field(default_factory=lambda: ["calc/__init__.py"])


class FakeHost:
    def __init__(self, comments=None, permission="write", labels=None):
        self.comments = comments or []
        self.permission = permission
        self.labels = labels if labels is not None else [cmd.WATCH_LABEL]

    def list_comments(self, repo, number, since=None):
        return list(self.comments)

    def permission_of(self, repo, login):
        return self.permission

    def get_pr(self, repo, number):
        return FakePr()

    def list_labels(self, repo, number):
        return list(self.labels)


class WorkItem:
    kind = "failing_check"


class WatchTests(unittest.TestCase):
    """The loop stops where it should, and never pushes after a cancel."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cs-watch-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.repo = self.root / "repo"
        (self.repo / "calc").mkdir(parents=True)
        (self.repo / "calc" / "__init__.py").write_text("x = 1\n")
        for cmdline in (["git", "init", "-q"], ["git", "add", "-A"],
                        ["git", "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "b"]):
            subprocess.run(cmdline, cwd=str(self.repo), check=True, capture_output=True)

    def _watch(self, host, *, worker=None, token=None, cancel=None, max_rounds=3, label_present=None):
        from code_shipper.steward.policy import ScopePolicy

        class NoopWorker:
            def run(self, *, workspace, item, prompt, budget):
                return {}

        return watch(
            repo="o/r",
            pr_number=1,
            host=host,
            worker=worker or NoopWorker(),
            policy=ScopePolicy(allowed=("calc/**/*.py",)),
            budget=RunBudget(fix_rounds=max_rounds, clock=lambda: 0.0),
            repo_dir=self.repo,
            token=token,
            cancel=cancel,
            max_rounds=max_rounds,
            label_present=label_present,
        )

    def test_a_stop_command_ends_the_loop_before_any_work(self) -> None:
        host = FakeHost(comments=[FakeComment(1, "@code-shipper stop", "alice")])
        receipt = self._watch(host)
        self.assertTrue(receipt.stopped_because.startswith("stop command"))
        self.assertEqual(receipt.rounds, [], "no work should start after a stop")

    def test_a_removed_label_ends_the_loop(self) -> None:
        receipt = self._watch(FakeHost(), label_present=lambda: False)
        self.assertIn("label", receipt.stopped_because)
        self.assertEqual(receipt.rounds, [])

    def test_a_stop_from_a_reader_does_not_end_the_loop(self) -> None:
        host = FakeHost(comments=[FakeComment(1, "@code-shipper stop", "bob")], permission="read")
        receipt = self._watch(host)
        self.assertNotIn("stop command", receipt.stopped_because)

    def test_an_instruction_is_recorded_not_executed_as_a_fix(self) -> None:
        host = FakeHost(comments=[FakeComment(1, "@code-shipper rename x", "alice")])
        receipt = self._watch(host)
        self.assertEqual(receipt.rounds[0].verdict, "instruction")
        self.assertEqual(receipt.rounds[0].detail["text"], "rename x")

    def test_an_already_cancelled_run_does_no_work(self) -> None:
        cancel = CancelToken()
        cancel.cancel("stop before starting", by="alice")
        receipt = self._watch(FakeHost(), cancel=cancel)
        self.assertIn("cancelled", receipt.stopped_because)
        self.assertEqual(receipt.rounds, [])
        self.assertEqual(receipt.pushed, [])

    def test_the_loop_stops_when_there_is_nothing_to_do(self) -> None:
        receipt = self._watch(FakeHost())
        self.assertTrue(receipt.rounds, "at least one round ran")
        self.assertIn("nothing", receipt.stopped_because.lower())

    def test_the_budget_is_in_the_receipt(self) -> None:
        receipt = self._watch(FakeHost())
        self.assertIn("wall_clock_seconds", receipt.budget)

    def test_nothing_is_pushed_without_a_token(self) -> None:
        receipt = self._watch(FakeHost(), token=None)
        self.assertEqual(receipt.pushed, [])


if __name__ == "__main__":
    unittest.main()
