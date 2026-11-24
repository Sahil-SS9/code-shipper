"""Ledger tests, including T-UNIT-LEDGER.

T-UNIT-LEDGER: a crash between the write and its receipt must leave the entry ``uncertain`` and the
next tick must settle it rather than re-post. These tests simulate that at the ledger level: a write
recorded as ``posting`` and never confirmed stays unresolved, and is not replayed blindly.

Locking is tested with real processes, because an in-process lock would pass even if the mechanism
did nothing across processes — and two ticks working one PR is exactly what the lock exists to stop.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import sys
import unittest
from pathlib import Path

from code_shipper.ledger import Ledger, LedgerError
# The ledger lock uses fcntl (POSIX advisory locks) and the state-dir test asserts POSIX
# mode bits. Both are skipped on Windows rather than failing there.
posix_only = unittest.skipIf(sys.platform.startswith("win"), "POSIX-only test")



class LedgerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="cs-ledger-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))
        self.ledger = Ledger.for_pr(
            self.dir, host="github", owner="o", repo="r", number=7
        )

    def lines(self) -> list[str]:
        if not self.ledger.path.exists():
            return []
        return self.ledger.path.read_text().splitlines()


class PathTests(LedgerCase):
    def test_path_follows_the_documented_layout(self) -> None:
        self.assertEqual(
            self.ledger.path,
            self.dir / "prs" / "github" / "o" / "r" / "7.jsonl",
        )

    def test_rejects_path_traversal_in_components(self) -> None:
        for bad in ("..", "../etc", "a/b", ""):
            with self.subTest(component=bad):
                with self.assertRaises(LedgerError):
                    Ledger.for_pr(self.dir, host="github", owner=bad, repo="r", number=1)

    def test_rejects_a_bad_pr_number(self) -> None:
        for bad in (0, -1, "7"):
            with self.subTest(number=bad):
                with self.assertRaises(LedgerError):
                    Ledger.for_pr(self.dir, host="github", owner="o", repo="r", number=bad)  # type: ignore[arg-type]


class AppendTests(LedgerCase):
    def test_append_writes_one_json_line(self) -> None:
        self.ledger.append("event", head="abc123", note="opened")
        lines = self.lines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["kind"], "event")
        self.assertEqual(record["head"], "abc123")
        self.assertIn("ts", record)

    def test_appends_accumulate_in_order(self) -> None:
        self.ledger.append("event", n=1)
        self.ledger.append("event", n=2)
        self.ledger.append("event", n=3)
        numbers = [json.loads(line)["n"] for line in self.lines()]
        self.assertEqual(numbers, [1, 2, 3])

    def test_unknown_kind_is_refused(self) -> None:
        with self.assertRaises(LedgerError):
            self.ledger.append("made_up", n=1)

    def test_oversized_entry_is_refused(self) -> None:
        with self.assertRaises(LedgerError):
            self.ledger.append("event", blob="x" * (300 * 1024))

    def test_entry_is_flushed_to_disk_immediately(self) -> None:
        # read through a second handle: a buffered write would not be visible
        self.ledger.append("event", head="abc")
        fresh = Ledger(self.ledger.path)
        self.assertEqual(len(fresh.entries()), 1)


class UncertainWriteTests(LedgerCase):
    """T-UNIT-LEDGER."""

    def test_a_write_left_posting_is_unresolved(self) -> None:
        self.ledger.record_write(op_id="w1", op="comment", outcome="posting", head="a")
        pending = self.ledger.unresolved_writes()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["op_id"], "w1")

    def test_a_confirmed_write_is_not_unresolved(self) -> None:
        self.ledger.record_write(op_id="w1", op="comment", outcome="posting", head="a")
        self.ledger.record_write(op_id="w1", op="comment", outcome="confirmed", head="a")
        self.assertEqual(self.ledger.unresolved_writes(), [])

    def test_a_refused_write_is_not_unresolved(self) -> None:
        self.ledger.record_write(op_id="w1", op="comment", outcome="refused", head="a")
        self.assertEqual(self.ledger.unresolved_writes(), [])

    def test_uncertain_stays_open_until_settled(self) -> None:
        # the crash case: we posted and never learned the result
        self.ledger.record_write(op_id="w1", op="comment", outcome="posting", head="a")
        self.ledger.record_write(op_id="w1", op="comment", outcome="uncertain", head="a")
        self.assertEqual(len(self.ledger.unresolved_writes()), 1)
        self.ledger.record_write(op_id="w1", op="comment", outcome="confirmed", head="a")
        self.assertEqual(self.ledger.unresolved_writes(), [])

    def test_independent_writes_are_tracked_separately(self) -> None:
        self.ledger.record_write(op_id="w1", op="comment", outcome="confirmed")
        self.ledger.record_write(op_id="w2", op="comment", outcome="uncertain")
        pending = self.ledger.unresolved_writes()
        self.assertEqual([p["op_id"] for p in pending], ["w2"])

    def test_unknown_outcome_is_refused(self) -> None:
        with self.assertRaises(LedgerError):
            self.ledger.record_write(op_id="w1", op="comment", outcome="probably")

    def test_a_truncated_final_line_does_not_lose_earlier_entries(self) -> None:
        self.ledger.append("event", n=1)
        self.ledger.append("event", n=2)
        # simulate a crash mid-write
        with self.ledger.path.open("a") as handle:
            handle.write('{"ts": 1.0, "kind": "event", "n": 3')
        entries = self.ledger.entries()
        self.assertEqual([e.data.get("n") for e in entries], [1, 2])


class HeadAndBudgetTests(LedgerCase):
    def test_last_head_reads_the_newest_entry_that_has_one(self) -> None:
        self.ledger.append("event", head="aaa")
        self.ledger.append("event", note="no head here")
        self.assertEqual(self.ledger.last_head(), "aaa")

    def test_last_head_is_none_on_an_empty_ledger(self) -> None:
        self.assertIsNone(self.ledger.last_head())

    def test_budget_sums_the_current_utc_day(self) -> None:
        now = time.time()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        self.ledger.append("budget", day=day, cost_usd=0.25)
        self.ledger.append("budget", day=day, cost_usd=0.5)
        totals = self.ledger.budget_today(now=now)
        self.assertAlmostEqual(totals["spent_usd"], 0.75)
        self.assertEqual(totals["unknown_cost_runs"], 0)

    def test_unknown_cost_is_counted_separately_not_as_zero(self) -> None:
        now = time.time()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        self.ledger.append("budget", day=day, cost_usd=None)
        totals = self.ledger.budget_today(now=now)
        self.assertEqual(totals["spent_usd"], 0)
        self.assertEqual(totals["unknown_cost_runs"], 1)


@posix_only
class LockTests(unittest.TestCase):
    """A real second process, because an in-process lock would prove nothing."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="cs-lock-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))
        self.ledger = Ledger.for_pr(self.dir, host="github", owner="o", repo="r", number=1)
        self.repo_root = Path(__file__).resolve().parent.parent

    def spawn_holder(self, hold_seconds: float) -> subprocess.Popen:
        script = textwrap.dedent(
            f"""
            import sys, time
            sys.path.insert(0, {str(self.repo_root)!r})
            from code_shipper.ledger import Ledger
            led = Ledger({str(self.ledger.path)!r})
            with led.lock(timeout=5):
                print("held", flush=True)
                time.sleep({hold_seconds})
            """
        )
        return subprocess.Popen(
            [sys.executable, "-B", "-c", script],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    def test_a_second_holder_times_out_while_the_first_holds(self) -> None:
        holder = self.spawn_holder(3.0)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "held")
            with self.assertRaises(LedgerError) as caught:
                with self.ledger.lock(timeout=0.3):
                    self.fail("should not have acquired the lock")
            self.assertIn("timed out", str(caught.exception))
        finally:
            holder.kill()
            holder.communicate()

    def test_the_lock_is_released_when_the_holder_dies(self) -> None:
        holder = self.spawn_holder(60.0)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        holder.kill()  # an advisory lock is released by the kernel on death
        holder.communicate()
        with self.ledger.lock(timeout=5):
            pass  # acquired: a pid-file lock would have been stuck forever

    def test_the_lock_can_be_taken_again_after_release(self) -> None:
        for _ in range(3):
            with self.ledger.lock(timeout=5):
                pass


@posix_only
class PathPermissionTests(unittest.TestCase):
    def test_state_directory_is_owner_only(self) -> None:
        from code_shipper.paths import ensure_dir

        base = Path(tempfile.mkdtemp(prefix="cs-state-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(base, ignore_errors=True))
        made = ensure_dir(base / "state")
        self.assertEqual(made.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
