"""N2: three tries per problem, then stop and report.

The watch loop and the CLI are exercised with the fix itself replaced, because what is under test is
the counting and the hand-back, not the container. `run_fix` is patched where `watch` imports it, and
classification is patched where `watch` calls it, so the loop's own code runs unchanged.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

from code_shipper.budget import RunBudget
from code_shipper.steward import watch as watch_module
from code_shipper.steward.attempts import (
    MAX_ATTEMPTS_PER_PROBLEM,
    AttemptLog,
    needs_you_comment,
    problem_key,
)
from code_shipper.steward.classify import WorkItem
from code_shipper.steward.fix import FixReceipt
from code_shipper.steward.policy import ScopePolicy


@dataclass
class FakePR:
    title: str = "t"
    base_sha: str = "b" * 40
    head_sha: str = "a" * 40
    head_ref: str = "feature"
    mergeable_state: str = "clean"
    changed_files: list = field(default_factory=list)


class FakeHost:
    def get_pr(self, repo, number):
        return FakePR()

    def list_comments(self, repo, number, since=None):
        return []

    def permission_of(self, repo, login):
        return "none"

    def list_labels(self, repo, number):
        return ["code-shipper-watching"]


FAILING_CHECK = WorkItem(kind="failing_check", reason="unit tests fail", source_id="tests")
OTHER = WorkItem(kind="failing_check", reason="lint fails", source_id="lint")


def _run(items_each_round, *, attempts=None, rounds=10, verdict="pushed"):
    worked: list[str] = []

    def fake_run_fix(*, items, **_kwargs):
        receipt = FixReceipt()
        if not items:
            receipt.verdict, receipt.ok = "nothing_to_do", True
            return receipt
        worked.append(items[0].source_id)
        receipt.item = items[0].as_dict()
        receipt.verdict = verdict
        receipt.ok = verdict == "pushed"
        receipt.push = {"ok": receipt.ok, "new_sha": "c" * 40}
        receipt.problems = [] if receipt.ok else ["tests still fail"]
        return receipt

    with tempfile.TemporaryDirectory() as tmp, \
            mock.patch.object(watch_module, "run_fix", side_effect=fake_run_fix), \
            mock.patch("code_shipper.steward.classify.classify", side_effect=lambda **_: items_each_round()):
        receipt = watch_module.watch(
            repo="o/n", pr_number=1, host=FakeHost(), worker=None,
            policy=ScopePolicy(allowed=("**",)),
            budget=RunBudget(fix_rounds=50, pushes_per_day=50, wall_clock_seconds=600),
            repo_dir=Path(tmp), token="t", max_rounds=rounds, attempts=attempts,
        )
    return receipt, worked


class TheCapTests(unittest.TestCase):
    def test_a_problem_that_keeps_coming_back_is_worked_three_times_then_handed_back(self) -> None:
        receipt, worked = _run(lambda: [FAILING_CHECK])
        self.assertEqual(worked, ["tests"] * MAX_ATTEMPTS_PER_PROBLEM)
        self.assertIn("needs you", receipt.stopped_because)
        self.assertEqual(len(receipt.needs_you), 1)
        self.assertEqual(len(receipt.needs_you[0]["attempts"]), 3)

    def test_failed_attempts_count_as_well_as_pushes(self) -> None:
        receipt, worked = _run(lambda: [FAILING_CHECK], verdict="out_of_scope")
        self.assertEqual(len(worked), 3)
        self.assertEqual([a["verdict"] for a in receipt.needs_you[0]["attempts"]], ["out_of_scope"] * 3)
        self.assertIn("tests still fail", receipt.needs_you[0]["attempts"][0]["detail"])

    def test_another_problem_is_still_worked_after_one_is_handed_back(self) -> None:
        receipt, worked = _run(lambda: [FAILING_CHECK, OTHER], rounds=10)
        self.assertEqual(worked.count("tests"), 3)
        self.assertEqual(worked.count("lint"), 3)
        self.assertEqual({e["key"] for e in receipt.needs_you},
                         {problem_key(FAILING_CHECK), problem_key(OTHER)})

    def test_a_problem_that_goes_away_is_not_reported(self) -> None:
        offered = iter([[FAILING_CHECK], [FAILING_CHECK], []])
        receipt, worked = _run(lambda: next(offered, []))
        self.assertEqual(len(worked), 2)
        self.assertEqual(receipt.needs_you, [])

    def test_the_cap_holds_when_the_caller_passes_no_log(self) -> None:
        """Not an opt-in: omitting `attempts` must not switch the cap off."""
        receipt, worked = _run(lambda: [FAILING_CHECK], attempts=None, rounds=10)
        self.assertEqual(len(worked), 3)

    def test_the_count_survives_between_watches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "1.json"
            _run(lambda: [FAILING_CHECK], attempts=AttemptLog.load(path), rounds=2)
            receipt, worked = _run(lambda: [FAILING_CHECK], attempts=AttemptLog.load(path), rounds=10)
            self.assertEqual(len(worked), 1, "the second watch should only have one try left")
            self.assertIn("needs you", receipt.stopped_because)

    def test_mutation_a_limit_above_the_rounds_never_hands_back(self) -> None:
        """If the cap is what stops it, raising the cap must let the loop run to max_rounds."""
        receipt, worked = _run(lambda: [FAILING_CHECK], attempts=AttemptLog(limit=99), rounds=5)
        self.assertEqual(len(worked), 5)
        self.assertEqual(receipt.needs_you, [])


class TheReportTests(unittest.TestCase):
    def test_the_comment_lists_every_attempt_and_says_it_stopped(self) -> None:
        log = AttemptLog()
        for verdict in ("pushed", "out_of_scope", "worker_failed"):
            log.record(FAILING_CHECK, verdict=verdict, detail=f"why {verdict}")
        body = needs_you_comment([{"key": problem_key(FAILING_CHECK), "reason": FAILING_CHECK.reason,
                                   "attempts": log.attempts(problem_key(FAILING_CHECK))}])
        self.assertIn("needs you", body)
        self.assertIn("tried 3 times", body)
        for verdict in ("pushed", "out_of_scope", "worker_failed"):
            self.assertIn(verdict, body)
        self.assertIn("never approves or merges", body)

    def test_the_cli_posts_once_and_never_in_a_dry_run(self) -> None:
        import argparse

        from code_shipper import cli

        log = AttemptLog()
        for _ in range(3):
            log.record(FAILING_CHECK, verdict="pushed")
        entry = {"key": problem_key(FAILING_CHECK), "reason": "unit tests fail",
                 "attempts": log.attempts(problem_key(FAILING_CHECK)), "reported": False}
        receipt = mock.Mock(needs_you=[entry])
        posted = []

        class FakeBroker:
            def __init__(self, **_):
                pass

            def post_comment(self, **kwargs):
                posted.append(kwargs)

        with mock.patch("code_shipper.broker.roles.Broker", FakeBroker):
            cli._report_needs_you(argparse.Namespace(dry_run=True, repo="o/n", pr=1), receipt, log, "t")
            self.assertEqual(posted, [])
            cli._report_needs_you(argparse.Namespace(dry_run=False, repo="o/n", pr=1), receipt, log, "t")
            cli._report_needs_you(argparse.Namespace(dry_run=False, repo="o/n", pr=1), receipt, log, "t")
        self.assertEqual(len(posted), 1)
        self.assertIn("needs you", posted[0]["body"])
        self.assertTrue(log.reported(problem_key(FAILING_CHECK)))


if __name__ == "__main__":
    unittest.main()
