"""D16e: retention applied to the records that actually exist — the ledger.

The five cases, restated against real ledger lines:

1. an old, settled ledger for a closed pull request is pruned
2. an old ledger for an open pull request is kept
3. an old ledger holding an uncertain write is kept
4. a ledger inside the retention window is kept
5. pruning one repository never touches another

Every line here is produced by `Ledger.record_write`, the writer the product uses, so the field names
cannot drift from the writer's. The previous version asserted against `{"pr": {"state": ...}}`, a shape
nothing writes — which is why it passed while the feature pruned a directory that did not exist.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from code_shipper.ledger import Ledger
from code_shipper.receipts import (
    RETENTION_DAYS,
    prune_all,
    prune_pr_ledger,
    prune_repo_ledgers,
)

DAY = 86400.0
NOW = 1_800_000_000.0  # fixed, so aging is deterministic
HOST = "github.com"


def ledger_path(state_dir: str, owner: str, repo: str, number: int) -> str:
    return os.path.join(state_dir, "prs", HOST, owner, repo, f"{number}.jsonl")


def _age(path: str, age_days: float) -> None:
    """Rewrite every entry's `ts`, which is what retention reads.

    Aging the record rather than the file's mtime is deliberate: mtime does not survive a copy or a
    restore, and the product records its own timestamp for exactly that reason.
    """
    stamp = NOW - age_days * DAY
    lines = []
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        entry = json.loads(line)
        entry["ts"] = stamp
        lines.append(json.dumps(entry))
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")


def real_ledger(state_dir: str, owner: str, repo: str, number: int, outcome: str, *,
                age_days: float, extra: dict | None = None) -> str:
    """One ledger written by the real writer, then aged. Returns its path."""
    ledger = Ledger.for_pr(Path(state_dir), host=HOST, owner=owner, repo=repo, number=number)
    ledger.record_write(op=f"op-{number}", outcome=outcome, head="a" * 40, **(extra or {}))
    path = str(ledger.path)
    _age(path, age_days)
    return path


class TheFiveSpecifiedCasesTests(unittest.TestCase):
    def test_1_an_old_settled_ledger_for_a_closed_pr_is_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as state:
            path = real_ledger(state, "o", "n", 7, "confirmed", age_days=40)
            self.assertTrue(os.path.exists(path))
            result = prune_repo_ledgers(state_dir=state, owner="o", repo="n",
                                        closed_prs={7}, now=NOW)
            self.assertEqual(result[7].removed_count, 1)
            self.assertFalse(os.path.exists(path))

    def test_2_an_old_ledger_for_an_open_pr_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as state:
            path = real_ledger(state, "o", "n", 8, "confirmed", age_days=40)
            result = prune_repo_ledgers(state_dir=state, owner="o", repo="n",
                                        closed_prs=frozenset(), now=NOW)
            self.assertEqual(result[8].removed_count, 0)
            self.assertEqual(result[8].kept_protected[0][1],
                             "the pull request is not known to be closed")
            self.assertTrue(os.path.exists(path))

    def test_3_an_old_unsettled_write_is_kept_whatever_the_pr_state(self) -> None:
        """The case that matters most: a write that may have landed must never lose its record."""
        for outcome in ("planned", "posting", "uncertain"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as state:
                path = real_ledger(state, "o", "n", 9, outcome, age_days=400)
                result = prune_repo_ledgers(state_dir=state, owner="o", repo="n",
                                            closed_prs={9}, now=NOW)
                self.assertEqual(result[9].removed_count, 0)
                self.assertIn(outcome, result[9].kept_protected[0][1])
                self.assertTrue(os.path.exists(path), f"a {outcome} write lost its record")

    def test_4_a_ledger_inside_the_window_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as state:
            path = real_ledger(state, "o", "n", 10, "confirmed", age_days=RETENTION_DAYS - 1)
            result = prune_repo_ledgers(state_dir=state, owner="o", repo="n",
                                        closed_prs={10}, now=NOW)
            self.assertEqual(result[10].removed_count, 0)
            self.assertEqual(result[10].kept_young, 1)
            self.assertTrue(os.path.exists(path))

    def test_5_pruning_one_repo_never_touches_another(self) -> None:
        with tempfile.TemporaryDirectory() as state:
            keep = real_ledger(state, "o", "other", 7, "confirmed", age_days=40)
            drop = real_ledger(state, "o", "n", 7, "confirmed", age_days=40)
            result = prune_all(state_dir=state, closed_prs_by_repo={"o/n": {7}}, now=NOW)
            self.assertIn("o/n", result)
            self.assertEqual(result["o/n"][7].removed_count, 1)
            self.assertFalse(os.path.exists(drop))
            self.assertTrue(os.path.exists(keep), "another repository's ledger was pruned")


class TheBoundariesAndExceptionsTests(unittest.TestCase):
    def test_exactly_at_the_retention_boundary_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as state:
            path = real_ledger(state, "o", "n", 11, "confirmed", age_days=RETENTION_DAYS)
            result = prune_repo_ledgers(state_dir=state, owner="o", repo="n", closed_prs={11}, now=NOW)
            self.assertEqual(result[11].removed_count, 0)
            self.assertTrue(os.path.exists(path))

    def test_one_second_past_the_boundary_is_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as state:
            path = real_ledger(state, "o", "n", 12, "confirmed", age_days=RETENTION_DAYS + 1 / DAY)
            result = prune_repo_ledgers(state_dir=state, owner="o", repo="n", closed_prs={12}, now=NOW)
            self.assertEqual(result[12].removed_count, 1)
            self.assertFalse(os.path.exists(path))

    def test_a_refused_write_is_settled_and_may_be_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as state:
            path = real_ledger(state, "o", "n", 13, "refused", age_days=60)
            result = prune_repo_ledgers(state_dir=state, owner="o", repo="n", closed_prs={13}, now=NOW)
            self.assertEqual(result[13].removed_count, 1)
            self.assertFalse(os.path.exists(path))

    def test_an_unrecognised_outcome_is_kept(self) -> None:
        """Fail closed: a state the code does not understand must not be read as settled."""
        with tempfile.TemporaryDirectory() as state:
            path = real_ledger(state, "o", "n", 14, "confirmed", age_days=60)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"ts": NOW - 60 * DAY, "kind": "write",
                                         "op_id": "x", "outcome": "half-posted"}) + "\n")
            result = prune_repo_ledgers(state_dir=state, owner="o", repo="n", closed_prs={14}, now=NOW)
            self.assertEqual(result[14].removed_count, 0)
            self.assertIn("unrecognised", result[14].kept_protected[0][1])
            self.assertTrue(os.path.exists(path))

    def test_a_write_with_no_outcome_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as state:
            path = real_ledger(state, "o", "n", 15, "confirmed", age_days=60)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"ts": NOW - 60 * DAY, "kind": "write", "op_id": "y"}) + "\n")
            result = prune_repo_ledgers(state_dir=state, owner="o", repo="n", closed_prs={15}, now=NOW)
            self.assertEqual(result[15].removed_count, 0)
            self.assertIn("no recorded outcome", result[15].kept_protected[0][1])

    def test_a_ledger_without_timestamps_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as state:
            path = real_ledger(state, "o", "n", 16, "confirmed", age_days=60)
            kept = []
            for line in open(path, encoding="utf-8"):
                if not line.strip():
                    continue
                entry = json.loads(line)
                entry.pop("ts", None)
                kept.append(json.dumps(entry))
            open(path, "w", encoding="utf-8").write("\n".join(kept) + "\n")
            result = prune_repo_ledgers(state_dir=state, owner="o", repo="n", closed_prs={16}, now=NOW)
            self.assertEqual(result[16].removed_count, 0)
            self.assertIn("age cannot be established", result[16].kept_protected[0][1])

    def test_an_unreadable_file_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as state:
            path = ledger_path(state, "o", "n", 17)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(b"\xff\xfe not utf-8")
            result = prune_repo_ledgers(state_dir=state, owner="o", repo="n", closed_prs={17}, now=NOW)
            self.assertEqual(result[17].removed_count, 0)
            self.assertIn("could not be read", result[17].kept_protected[0][1])
            self.assertTrue(os.path.exists(path))

    def test_a_missing_ledger_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as state:
            result = prune_pr_ledger(path=ledger_path(state, "o", "n", 99), closed=True)
            self.assertEqual(result.removed_count, 0)

    def test_a_path_traversal_in_a_name_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as state:
            for bad in ("../evil", "a/b", "..", ""):
                with self.subTest(repo=bad):
                    with self.assertRaises(ValueError):
                        prune_repo_ledgers(state_dir=state, owner="o", repo=bad,
                                           closed_prs={1}, now=NOW)

    def test_other_files_in_the_directory_are_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as state:
            real_ledger(state, "o", "n", 18, "confirmed", age_days=60)
            stray = os.path.join(os.path.dirname(ledger_path(state, "o", "n", 18)), "notes.txt")
            with open(stray, "w", encoding="utf-8") as handle:
                handle.write("keep me")
            prune_repo_ledgers(state_dir=state, owner="o", repo="n", closed_prs={18}, now=NOW)
            self.assertTrue(os.path.exists(stray))

    def test_the_retention_window_is_30_days(self) -> None:
        self.assertEqual(RETENTION_DAYS, 30)


class TheFeatureIsWiredToRealRecordsTests(unittest.TestCase):
    """The failure this round fixed: the old code pruned a directory nothing writes."""

    def test_nothing_still_prunes_a_receipts_directory(self) -> None:
        from pathlib import Path

        source_root = Path(__file__).resolve().parent.parent / "code_shipper"
        offenders = []
        for path in source_root.rglob("*.py"):
            if path.name == "receipts.py":
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                # A *path being built*, not a mention. The first version of this check flagged its own
                # explanatory comments and the module import, which is a false positive that would get the
                # rule deleted rather than obeyed.
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("-") or stripped.startswith("*"):
                    continue
                if "_dir" in stripped and "receipts" in stripped:
                    offenders.append(f"{path.name}: {stripped}")
        self.assertEqual(offenders, [], f"something still refers to a receipts directory: {offenders}")

    def test_the_retention_vocabulary_matches_the_ledgers(self) -> None:
        """The outcomes kept or pruned must be exactly the ones the ledger can record."""
        from code_shipper.ledger import OUTCOMES
        from code_shipper.receipts import OPEN_OUTCOMES, SETTLED_OUTCOMES

        self.assertEqual(OPEN_OUTCOMES | SETTLED_OUTCOMES, set(OUTCOMES),
                         "retention handles a different vocabulary from the one the ledger records")

    def test_it_runs_at_command_start_not_as_a_daemon(self) -> None:
        from pathlib import Path

        source = (Path(__file__).resolve().parent.parent
                  / "code_shipper" / "receipts.py").read_text(encoding="utf-8")
        for forbidden in ("Thread", "Timer", "schedule.every", "while True"):
            self.assertNotIn(forbidden, source)


class PruningIsLoggedTests(unittest.TestCase):
    def test_a_pruning_run_appends_a_log_line_per_repo(self) -> None:
        with tempfile.TemporaryDirectory() as state:
            real_ledger(state, "o", "n", 20, "confirmed", age_days=60)
            log: list[str] = []
            prune_all(state_dir=state, closed_prs_by_repo={"o/n": {20}}, now=NOW, log=log)
            self.assertTrue(log)
            self.assertIn("o/n", log[0])
            self.assertIn("pruned 1", log[0])

    def test_a_quiet_repo_logs_nothing(self) -> None:
        """No deletions and nothing protected means nothing worth a log line."""
        with tempfile.TemporaryDirectory() as state:
            real_ledger(state, "o", "n", 21, "confirmed", age_days=1)
            log: list[str] = []
            prune_all(state_dir=state, closed_prs_by_repo={"o/n": {21}}, now=NOW, log=log)
            self.assertEqual(log, [])


class TheCommandSuppliesClosureTests(unittest.TestCase):
    """The condition on Part 1: retention was correct but its only real caller never said what was closed,
    so it pruned nothing, ever. These go through `cli._prune_receipts_once`, the function `main` calls."""

    def _run(self, state: str, host, *, token: str | None = "t0ken") -> list[str]:
        import argparse
        import contextlib
        import io
        import time
        from unittest import mock

        from code_shipper import cli

        env = {"XDG_STATE_HOME": state}
        if token:
            env["CODE_SHIPPER_GITHUB_TOKEN"] = token
        err = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(cli, "_closure_host", return_value=host), \
                mock.patch.object(cli, "_read_token_quietly", return_value=None), \
                contextlib.redirect_stderr(err):
            if not token:
                os.environ.pop("CODE_SHIPPER_GITHUB_TOKEN", None)
            cli._prune_receipts_once(argparse.Namespace(command="review"))
        self._now = time.time()
        return err.getvalue().splitlines()

    @staticmethod
    def _ledger(state: str, number: int, outcome: str, age_days: float) -> str:
        import time

        path = real_ledger(os.path.join(state, "code-shipper"), "o", "n", number, outcome, age_days=0)
        lines = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
        for entry in lines:
            entry["ts"] = time.time() - age_days * DAY
        open(path, "w", encoding="utf-8").write("\n".join(json.dumps(e) for e in lines) + "\n")
        return path

    class _Host:
        def __init__(self, states=None, error=None):
            self.states, self.error, self.asked = states or {}, error, []

        def get_pr(self, repo, number):
            self.asked.append((repo, number))
            if self.error:
                raise self.error

            class _PR:
                state = self.states.get(number, "open")
            return _PR()

    def test_an_old_settled_record_on_a_closed_pr_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as state:
            path = self._ledger(state, 5, "confirmed", age_days=45)
            host = self._Host({5: "closed"})
            self._run(state, host)
            self.assertEqual(host.asked, [("o/n", 5)])
            self.assertFalse(os.path.exists(path))

    def test_it_is_kept_when_the_reader_errors(self) -> None:
        with tempfile.TemporaryDirectory() as state:
            path = self._ledger(state, 6, "confirmed", age_days=45)
            lines = self._run(state, self._Host(error=RuntimeError("network down")))
            self.assertTrue(os.path.exists(path))
            self.assertTrue(any("state unknown" in line for line in lines), lines)

    def test_it_is_kept_while_the_pr_is_open(self) -> None:
        with tempfile.TemporaryDirectory() as state:
            path = self._ledger(state, 7, "confirmed", age_days=45)
            self._run(state, self._Host({7: "open"}))
            self.assertTrue(os.path.exists(path))

    def test_it_is_kept_without_a_token_and_says_why(self) -> None:
        with tempfile.TemporaryDirectory() as state:
            path = self._ledger(state, 8, "confirmed", age_days=45)
            host = self._Host({8: "closed"})
            lines = self._run(state, host, token=None)
            self.assertEqual(host.asked, [])
            self.assertTrue(os.path.exists(path))
            self.assertTrue(any("no GitHub token" in line for line in lines), lines)

    def test_no_request_is_made_when_nothing_is_old_and_settled(self) -> None:
        """Young records and unsettled writes cannot be pruned whatever GitHub says, so nobody asks."""
        with tempfile.TemporaryDirectory() as state:
            self._ledger(state, 9, "confirmed", age_days=3)
            self._ledger(state, 10, "uncertain", age_days=90)
            host = self._Host({9: "closed", 10: "closed"})
            self._run(state, host)
            self.assertEqual(host.asked, [])

    def test_the_number_of_questions_per_run_is_capped(self) -> None:
        from code_shipper.receipts import resolve_closed

        asked = []
        log: list[str] = []
        closed = resolve_closed([("o/n", n) for n in range(30)],
                                lambda repo, n: asked.append(n) or "closed", cap=20, log=log)
        self.assertEqual(len(asked), 20)
        self.assertEqual(len(closed["o/n"]), 20)
        self.assertIn("capped at 20", log[-1])

    def test_only_exactly_closed_counts(self) -> None:
        from code_shipper.receipts import resolve_closed

        for answer in ("CLOSED", "merged", "", None, "open"):
            self.assertEqual(resolve_closed([("o/n", 1)], lambda r, n, a=answer: a), {}, answer)


if __name__ == "__main__":
    unittest.main()
