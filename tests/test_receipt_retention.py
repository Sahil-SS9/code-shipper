"""D16e: the five retention cases, exactly as specified.

1. an old receipt for a closed PR is pruned
2. an old receipt for an open PR is kept
3. an old uncertain-write receipt is kept
4. a 29-day receipt is kept
5. pruning one repo never touches another
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest

from code_shipper.receipts import RETENTION_DAYS, prune_all, prune_repo_receipts

DAY = 86400.0
NOW = 1_800_000_000.0  # fixed, so aging is deterministic


def write_receipt(root: str, repo: str, name: str, *, age_days: float, body: dict | None = None) -> str:
    directory = os.path.join(root, repo.replace("/", "__"))
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    with open(path, "w") as handle:
        json.dump(body if body is not None else {"pr": {"number": 1, "state": "closed"}}, handle)
    stamp = NOW - age_days * DAY
    os.utime(path, (stamp, stamp))
    return path


class TheFiveSpecifiedCasesTests(unittest.TestCase):
    def test_1_an_old_receipt_for_a_closed_pr_is_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = write_receipt(root, "o/n", "closed.json", age_days=40,
                                 body={"pr": {"number": 7, "state": "closed"}})
            result = prune_repo_receipts(receipts_dir=root, repo="o/n", now=NOW)
            self.assertFalse(os.path.exists(path))
            self.assertEqual(result.removed, ("closed.json",))

    def test_2_an_old_receipt_for_an_open_pr_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = write_receipt(root, "o/n", "open.json", age_days=400,
                                 body={"pr": {"number": 7, "state": "open"}})
            result = prune_repo_receipts(receipts_dir=root, repo="o/n", now=NOW)
            self.assertTrue(os.path.exists(path), "an open PR's receipt must survive any age")
            self.assertEqual(result.removed, ())
            self.assertIn("open", result.kept_protected[0][1])

    def test_3_an_old_uncertain_write_receipt_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = write_receipt(root, "o/n", "uncertain.json", age_days=400,
                                 body={"pr": {"number": 7, "state": "closed"},
                                       "push": {"verdict": "uncertain"}})
            result = prune_repo_receipts(receipts_dir=root, repo="o/n", now=NOW)
            self.assertTrue(os.path.exists(path), "an uncertain write must never lose its receipt")
            self.assertEqual(result.removed, ())
            self.assertIn("uncertain", result.kept_protected[0][1])

    def test_3b_an_unreconciled_write_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = write_receipt(root, "o/n", "unrec.json", age_days=90,
                                 body={"pr": {"state": "closed"}, "needs_reconciliation": True})
            prune_repo_receipts(receipts_dir=root, repo="o/n", now=NOW)
            self.assertTrue(os.path.exists(path))

    def test_4_a_29_day_receipt_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = write_receipt(root, "o/n", "recent.json", age_days=29)
            result = prune_repo_receipts(receipts_dir=root, repo="o/n", now=NOW)
            self.assertTrue(os.path.exists(path))
            self.assertEqual(result.kept_young, 1)

    def test_4b_a_31_day_receipt_for_a_closed_pr_is_pruned(self) -> None:
        """The boundary is the retention window, so prove the other side of 30."""
        with tempfile.TemporaryDirectory() as root:
            path = write_receipt(root, "o/n", "just-old.json", age_days=31)
            prune_repo_receipts(receipts_dir=root, repo="o/n", now=NOW)
            self.assertFalse(os.path.exists(path))

    def test_5_pruning_one_repo_never_touches_another(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            mine = write_receipt(root, "o/n", "old.json", age_days=60)
            theirs = write_receipt(root, "o/other", "old.json", age_days=60)
            prune_repo_receipts(receipts_dir=root, repo="o/n", now=NOW)
            self.assertFalse(os.path.exists(mine))
            self.assertTrue(os.path.exists(theirs), "another repository's receipts were touched")

    def test_5b_prune_all_is_also_per_repo(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            a = write_receipt(root, "a/one", "old.json", age_days=60)
            b = write_receipt(root, "b/two", "old.json", age_days=60, body={"pr": {"state": "open"}})
            results = prune_all(receipts_dir=root, now=NOW)
            self.assertFalse(os.path.exists(a))
            self.assertTrue(os.path.exists(b))
            self.assertEqual(set(results), {"a/one", "b/two"})


class TheExceptionsAlwaysWinTests(unittest.TestCase):
    def test_an_open_pr_beats_any_age(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = write_receipt(root, "o/n", "ancient.json", age_days=5000,
                                 body={"pr": {"state": "OPEN"}})
            prune_repo_receipts(receipts_dir=root, repo="o/n", now=NOW)
            self.assertTrue(os.path.exists(path), "state matching must be case-insensitive")

    def test_an_unreadable_receipt_is_kept_not_deleted(self) -> None:
        """A receipt we cannot parse cannot be shown safe to delete."""
        with tempfile.TemporaryDirectory() as root:
            directory = os.path.join(root, "o__n")
            os.makedirs(directory)
            path = os.path.join(directory, "broken.json")
            with open(path, "w") as handle:
                handle.write("{not json")
            stamp = NOW - 90 * DAY
            os.utime(path, (stamp, stamp))
            result = prune_repo_receipts(receipts_dir=root, repo="o/n", now=NOW)
            self.assertTrue(os.path.exists(path))
            self.assertEqual(result.removed, ())
            self.assertIn("could not be read", result.kept_protected[0][1])

    def test_non_json_files_are_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            directory = os.path.join(root, "o__n")
            os.makedirs(directory)
            path = os.path.join(directory, "notes.txt")
            with open(path, "w") as handle:
                handle.write("x")
            stamp = NOW - 90 * DAY
            os.utime(path, (stamp, stamp))
            prune_repo_receipts(receipts_dir=root, repo="o/n", now=NOW)
            self.assertTrue(os.path.exists(path))

    def test_the_retention_window_is_30_days(self) -> None:
        self.assertEqual(RETENTION_DAYS, 30)


class PruningIsLoggedTests(unittest.TestCase):
    def test_a_pruning_run_appends_a_log_line_per_repo(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            write_receipt(root, "o/n", "old.json", age_days=60)
            log: list[str] = []
            prune_all(receipts_dir=root, now=NOW, log=log)
            self.assertTrue(log)
            self.assertIn("o/n", log[0])
            self.assertIn("pruned 1", log[0])

    def test_a_quiet_repo_logs_nothing(self) -> None:
        """No deletions and nothing protected means nothing worth a log line."""
        with tempfile.TemporaryDirectory() as root:
            write_receipt(root, "o/n", "recent.json", age_days=1)
            log: list[str] = []
            prune_all(receipts_dir=root, now=NOW, log=log)
            self.assertEqual(log, [])

    def test_it_runs_at_command_start_not_as_a_daemon(self) -> None:
        """Structural: there must be no timer or thread anywhere in the module."""
        import pathlib

        source = (pathlib.Path(__file__).resolve().parent.parent
                  / "code_shipper" / "receipts.py").read_text()
        for forbidden in ("Thread", "Timer", "schedule.every", "while True"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
