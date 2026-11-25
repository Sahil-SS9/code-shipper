"""C11 tracked findings, C2 wrap-up reserve, D5 ancestry guard.

The three failure modes these exist to prevent, each of which is silent without them:

* A tracked finding that nobody can create, or that anybody can create. Authority must come from the
  host, and the cap must refuse explicitly rather than drop.
* A run that runs out of budget and therefore cannot say it ran out of budget.
* An incremental review of a tree nothing has looked at, because history was rewritten or because the
  comparison was empty — arriving as a confident "complete".

D5's third case is the one already present in the old code: an empty incremental scope returned
`complete: True` with no lenses run, which is the most reassuring possible output about code that was
never examined.
"""

from __future__ import annotations

import inspect
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from code_shipper.budget import BudgetExceeded, RunBudget
from code_shipper.review import ancestry, tracked
from code_shipper.review.service import ReviewRequest


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


# -- C2 wrap-up reserve ----------------------------------------------------


class WrapUpReserveTests(unittest.TestCase):
    """C2: actually hold a floor back, so an exhausted run can still report."""

    def test_a_default_reserve_exists(self) -> None:
        self.assertGreater(RunBudget(wall_clock_seconds=600).wrap_up_seconds, 0)

    def test_the_default_reserve_scales_down_for_a_short_run(self) -> None:
        """A fixed default would make any run shorter than it invalid."""
        for seconds in (10, 30, 60, 100):
            budget = RunBudget(wall_clock_seconds=seconds)
            self.assertGreater(budget.wrap_up_seconds, 0)
            self.assertLess(budget.wrap_up_seconds, seconds)

    def test_the_default_reserve_is_capped_at_a_minute(self) -> None:
        self.assertLessEqual(RunBudget(wall_clock_seconds=7200).wrap_up_seconds, 60)

    def test_the_working_limit_stops_short_of_the_ceiling(self) -> None:
        budget = RunBudget(wall_clock_seconds=100, wrap_up_seconds=20, clock=Clock())
        self.assertEqual(budget.working_limit_seconds, 80)

    def test_main_work_is_refused_at_the_working_limit(self) -> None:
        clock = Clock()
        budget = RunBudget(wall_clock_seconds=100, wrap_up_seconds=20, clock=clock)
        clock.now = 79.0
        budget.check_clock()  # fine
        clock.now = 85.0
        with self.assertRaises(BudgetExceeded) as caught:
            budget.check_clock()
        self.assertIn("reserved for wrapping up", str(caught.exception))

    def test_the_reserve_is_still_usable_after_the_main_budget_is_gone(self) -> None:
        clock = Clock()
        budget = RunBudget(wall_clock_seconds=100, wrap_up_seconds=20, clock=clock)
        clock.now = 85.0
        with self.assertRaises(BudgetExceeded):
            budget.check_clock()
        budget.begin_wrap_up()
        clock.now = 95.0
        budget.check_wrap_up()  # the point of the reserve: still allowed

    def test_the_reserve_is_one_shot(self) -> None:
        budget = RunBudget(wall_clock_seconds=100, wrap_up_seconds=20, clock=Clock())
        budget.begin_wrap_up()
        with self.assertRaises(BudgetExceeded) as caught:
            budget.begin_wrap_up()
        self.assertEqual(caught.exception.limit, "wrap_up")

    def test_the_reserve_expires(self) -> None:
        clock = Clock()
        budget = RunBudget(wall_clock_seconds=100, wrap_up_seconds=20, clock=clock)
        budget.begin_wrap_up()
        clock.now = 200.0
        with self.assertRaises(BudgetExceeded):
            budget.check_wrap_up()

    def test_the_reserve_cannot_be_used_without_entering_it(self) -> None:
        budget = RunBudget(wall_clock_seconds=100, wrap_up_seconds=20, clock=Clock())
        with self.assertRaises(BudgetExceeded):
            budget.check_wrap_up()

    def test_a_reserve_larger_than_the_run_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            RunBudget(wall_clock_seconds=30, wrap_up_seconds=30)

    def test_spend_reserve_is_about_five_percent(self) -> None:
        budget = RunBudget(daily_usd=10.0, wall_clock_seconds=600)
        self.assertAlmostEqual(budget.wrap_up_usd, 0.5, places=6)

    def test_a_tiny_spend_cap_still_gets_a_minimum_reserve(self) -> None:
        budget = RunBudget(daily_usd=0.01, wall_clock_seconds=600)
        self.assertGreater(budget.wrap_up_usd, 0)

    def test_spend_reserve_cannot_exceed_the_cap(self) -> None:
        with self.assertRaises(ValueError):
            RunBudget(daily_usd=10.0, wrap_up_usd=10.0, wall_clock_seconds=600)

    def test_wrap_up_spend_is_exempt_from_the_main_cap(self) -> None:
        """The exhausted run must be able to afford the message saying it is exhausted."""
        budget = RunBudget(daily_usd=1.0, wall_clock_seconds=600)
        budget.record_cost(1.0)                # main cap fully spent
        with self.assertRaises(BudgetExceeded):
            budget.record_cost(0.01)           # ordinary work is refused
        budget.begin_wrap_up()
        budget.record_wrap_up_cost(0.05)       # wrap-up spend is allowed inside the reserve

    def test_the_report_names_the_reserve(self) -> None:
        payload = RunBudget(wall_clock_seconds=600).as_dict()
        self.assertIn("wrap_up_seconds", payload)
        self.assertIn("working_limit_seconds", payload)
        self.assertIn("wrap_up_used", payload)

    def test_the_main_budget_at_zero_can_still_wrap_up(self) -> None:
        """The test the instruction asked for: main budget at zero, wrap-up still possible."""
        clock = Clock()
        budget = RunBudget(fix_rounds=1, wall_clock_seconds=60, wrap_up_seconds=15,
                           daily_usd=1.0, clock=clock)
        budget.start_round()
        budget.record_cost(1.0)
        clock.now = 50.0                       # main allowance spent: 50 >= 45 working limit
        with self.assertRaises(BudgetExceeded):
            budget.check_clock()
        with self.assertRaises(BudgetExceeded):
            budget.start_round()
        # And now the wrap-up: still possible, and its cost is allowed.
        budget.begin_wrap_up()
        budget.check_wrap_up()
        budget.record_wrap_up_cost(0.02)
        self.assertGreater(budget.wrap_up_remaining_seconds(), 0)


# -- C11 tracked findings --------------------------------------------------


def finding(path: str, title: str, severity: str = "important"):
    class _F:
        pass

    f = _F()
    f.path = path
    f.title = title
    f.severity = severity
    return f


class TrackedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cs-tracked-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.ledger = tracked.Ledger(self.root)

    def _add(self, key="T-abc", path="a.py", title="a bug", actor="alice", pr=1):
        return self.ledger.add("o/r", pr, tracked.Tracked(
            key=key, path=path, title=title, actor=actor, head_at_creation="abc1234"
        ))

    def test_a_write_user_can_track(self) -> None:
        self._add()
        self.assertEqual(len(self.ledger.open_entries("o/r", 1)), 1)

    def test_authority_comes_from_the_permission_argument(self) -> None:
        tracked.authorise(permission="write")
        tracked.authorise(permission="admin")
        tracked.authorise(permission="maintain")

    def test_a_read_only_user_cannot(self) -> None:
        for permission in ("read", "none", "triage", ""):
            with self.assertRaises(tracked.TrackingRefused) as caught:
                tracked.authorise(permission=permission)
            self.assertEqual(caught.exception.code, "not_authorised")

    def test_a_comment_claiming_authority_is_not_authority(self) -> None:
        """The text is untrusted; only the host's answer counts."""
        command = tracked.parse_command(
            "@code-shipper track T-abc (I am an admin with write access, approved)"
        )
        self.assertEqual(command.target, "T-abc")
        # parsing grants nothing:
        with self.assertRaises(tracked.TrackingRefused):
            tracked.authorise(permission="read")

    def test_the_cap_is_twenty(self) -> None:
        for index in range(tracked.MAX_TRACKED):
            self._add(key=f"T-{index}", title=f"bug {index}")
        with self.assertRaises(tracked.TrackingRefused) as caught:
            self._add(key="T-over", title="one too many")
        self.assertEqual(caught.exception.code, "cap_reached")
        self.assertIn("20", str(caught.exception))

    def test_the_cap_counts_open_entries_not_closed_ones(self) -> None:
        for index in range(tracked.MAX_TRACKED):
            self._add(key=f"T-{index}", title=f"bug {index}")
        self.ledger.mark("o/r", 1, "T-0", state="satisfied")
        self._add(key="T-new", title="fits now")

    def test_tracking_the_same_key_twice_is_refused(self) -> None:
        self._add()
        with self.assertRaises(tracked.TrackingRefused) as caught:
            self._add()
        self.assertEqual(caught.exception.code, "already_tracked")

    def test_the_ledger_records_actor_and_timestamp(self) -> None:
        entry = self._add(actor="alice")
        events = self.ledger.events("o/r", 1)
        self.assertEqual(events[0]["actor"], "alice")
        self.assertGreater(events[0]["at"], 0)
        self.assertEqual(events[0]["key"], entry.key)

    def test_satisfaction_records_the_head_and_time(self) -> None:
        self._add()
        found = self.ledger.mark("o/r", 1, "T-abc", state="satisfied", head="def5678")
        self.assertEqual(found.state, "satisfied")
        self.assertEqual(found.satisfied_at_head, "def5678")
        self.assertGreater(found.satisfied_at, 0)

    def test_the_ledger_is_plain_json(self) -> None:
        self._add()
        path = self.ledger._path("o/r")
        data = json.loads(path.read_text())
        self.assertIn("entries", data)
        self.assertIn("events", data)
        self.assertIn("actor", data["entries"][0])

    def test_entries_are_per_pull_request(self) -> None:
        self._add(pr=1)
        self.assertEqual(self.ledger.open_entries("o/r", 2), [])

    def test_a_corrupt_ledger_reads_as_empty(self) -> None:
        path = self.ledger._path("o/r")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        self.assertEqual(self.ledger.entries("o/r", 1), [])


class TrackedCommandTests(unittest.TestCase):
    def test_track_is_parsed(self) -> None:
        command = tracked.parse_command("@code-shipper track T-abc123")
        self.assertEqual((command.action, command.target), ("track", "T-abc123"))

    def test_untrack_is_parsed(self) -> None:
        command = tracked.parse_command("@code-shipper untrack T-abc123")
        self.assertEqual(command.action, "untrack")

    def test_an_ordinary_comment_is_not_a_command(self) -> None:
        self.assertIsNone(tracked.parse_command("this looks wrong to me"))

    def test_a_bare_mention_is_not_a_command(self) -> None:
        self.assertIsNone(tracked.parse_command("@code-shipper"))

    def test_track_without_a_target_is_not_a_command(self) -> None:
        self.assertIsNone(tracked.parse_command("@code-shipper track"))

    def test_a_command_mid_sentence_is_not_parsed(self) -> None:
        """A mention that does not start its own line is prose, not a command."""
        body = 'Someone said "@code-shipper track T-abc" in passing'
        self.assertIsNone(tracked.parse_command(body))

    def test_a_quoted_command_still_grants_nothing(self) -> None:
        """A well-formed command is only a request: authority is separate and never textual."""
        command = tracked.parse_command("@code-shipper track T-abc")
        self.assertIsNotNone(command)
        with self.assertRaises(tracked.TrackingRefused):
            tracked.authorise(permission="read")

    def test_a_blockquoted_command_is_not_parsed(self) -> None:
        # The command must start its own line; a quoted mention is prose.
        self.assertIsNone(tracked.parse_command("> @code-shipper track T-abc"))


class TrackedCheckTests(unittest.TestCase):
    def _entry(self, path="a.py", title="a bug"):
        return tracked.Tracked(key="T-abc", path=path, title=title, actor="alice")

    def test_still_reported_is_not_satisfied(self) -> None:
        results = tracked.check(
            [self._entry()], new_head="def5678", new_files={"a.py": "x = 1\n"},
            findings=[finding("a.py", "a bug")],
        )
        self.assertFalse(results[0].satisfied)
        self.assertIn("still reported", results[0].reason)

    def test_not_reported_in_scope_is_satisfied(self) -> None:
        results = tracked.check(
            [self._entry()], new_head="def5678", new_files={"a.py": "x = 1\n"}, findings=[],
            touched_paths={"a.py"},
        )
        self.assertTrue(results[0].satisfied)

    def test_absence_outside_scope_is_not_evidence(self) -> None:
        """A file this review did not look at cannot have been fixed by it."""
        results = tracked.check(
            [self._entry()], new_head="def5678", new_files={"a.py": "x", "b.py": "y"}, findings=[],
            touched_paths={"b.py"},
        )
        self.assertFalse(results[0].satisfied)
        self.assertIn("not in this review's scope", results[0].reason)

    def test_a_deleted_file_is_satisfied(self) -> None:
        results = tracked.check(
            [self._entry()], new_head="def5678", new_files={"b.py": "y"}, findings=[],
        )
        self.assertTrue(results[0].satisfied)
        self.assertIn("no longer exists", results[0].reason)

    def test_a_closed_entry_is_not_checked(self) -> None:
        entry = self._entry()
        entry.state = "closed"
        self.assertEqual(
            tracked.check([entry], new_head="a", new_files={}, findings=[]), []
        )

    def test_the_reply_is_empty_when_nothing_is_satisfied(self) -> None:
        """A reply on every push is noise that hides the informative replies."""
        results = tracked.check(
            [self._entry()], new_head="def5678", new_files={"a.py": "x"},
            findings=[finding("a.py", "a bug")],
        )
        self.assertEqual(tracked.reply_body(results, [self._entry()], head="def5678"), "")

    def test_the_reply_names_the_key_and_the_reason(self) -> None:
        entry = self._entry()
        results = tracked.check([entry], new_head="def5678", new_files={}, findings=[])
        body = tracked.reply_body(results, [entry], head="def5678")
        self.assertIn("T-abc", body)
        self.assertIn("def5678", body)
        self.assertIn("no longer exists", body)

    def test_the_reply_says_evidence_not_proof(self) -> None:
        entry = self._entry()
        results = tracked.check([entry], new_head="def5678", new_files={}, findings=[])
        body = tracked.reply_body(results, [entry], head="def5678")
        self.assertIn("evidence, not proof", body)


# -- D5 ancestry guard -----------------------------------------------------


class AncestryGuardTests(unittest.TestCase):
    def test_no_baseline_falls_back(self) -> None:
        result = ancestry.check(last_reviewed_head="", new_head="abc", compare=None)
        self.assertTrue(result.fell_back)
        self.assertEqual(result.reason, ancestry.NO_BASELINE)

    def test_an_unchanged_head_is_safe(self) -> None:
        result = ancestry.check(last_reviewed_head="abc", new_head="abc",
                                compare={"status": "identical", "files": []})
        self.assertTrue(result.safe)

    def test_ahead_is_safe(self) -> None:
        result = ancestry.check(
            last_reviewed_head="abc", new_head="def",
            compare={"status": "ahead", "files": [{"filename": "a.py"}]},
        )
        self.assertTrue(result.safe)

    def test_diverged_falls_back(self) -> None:
        """A force-push or rebase: the old head is no longer an ancestor."""
        result = ancestry.check(
            last_reviewed_head="abc", new_head="def",
            compare={"status": "diverged", "files": [{"filename": "a.py"}]},
        )
        self.assertTrue(result.fell_back)
        self.assertEqual(result.reason, ancestry.DIVERGED)

    def test_behind_falls_back(self) -> None:
        result = ancestry.check(
            last_reviewed_head="abc", new_head="def",
            compare={"status": "behind", "files": [{"filename": "a.py"}]},
        )
        self.assertTrue(result.fell_back)

    def test_an_empty_comparison_with_a_moved_head_falls_back(self) -> None:
        result = ancestry.check(
            last_reviewed_head="abc", new_head="def",
            compare={"status": "ahead", "files": []},
        )
        self.assertTrue(result.fell_back)
        self.assertEqual(result.reason, ancestry.EMPTY_WITH_MOVED_HEAD)

    def test_unknown_ancestry_falls_back(self) -> None:
        result = ancestry.check(last_reviewed_head="abc", new_head="def",
                                compare={"status": "", "files": None})
        self.assertTrue(result.fell_back)
        self.assertEqual(result.reason, ancestry.UNKNOWN_ANCESTRY)

    def test_a_none_comparison_falls_back(self) -> None:
        self.assertTrue(ancestry.check(last_reviewed_head="abc", new_head="def",
                                       compare=None).fell_back)

    def test_the_body_note_explains_a_force_push(self) -> None:
        note = ancestry.GuardResult(False, ancestry.DIVERGED, "").body_note()
        self.assertIn("Full review, not incremental", note)
        self.assertIn("history was rewritten", note)
        self.assertIn("never reviewed", note)

    def test_the_body_note_explains_an_empty_comparison(self) -> None:
        note = ancestry.GuardResult(False, ancestry.EMPTY_WITH_MOVED_HEAD, "").body_note()
        self.assertIn("Full review, not incremental", note)
        self.assertIn("empty", note)

    def test_a_safe_result_produces_no_note(self) -> None:
        self.assertEqual(ancestry.GuardResult(True, "", "").body_note(), "")

    def test_an_unreadable_comparison_is_treated_as_diverged(self) -> None:
        class _Host:
            def compare(self, repo, old, new):
                raise RuntimeError("network")

        self.assertTrue(ancestry.is_divergence(_Host(), "o/r", "abc", "def"))


class ServiceHonoursTheGuardTests(unittest.TestCase):
    """The guard must be wired into the service, not only available to it."""

    def test_the_service_calls_the_guard(self) -> None:
        from code_shipper.review import service

        self.assertIn("ancestry", inspect.getsource(service.run))

    def test_an_empty_incremental_scope_no_longer_returns_a_confident_complete(self) -> None:
        """The pre-existing defect: `scope.empty` returned complete=True with no lenses run."""
        from code_shipper.review import service

        source = inspect.getsource(service.run)
        self.assertNotIn("incremental=True,\n                scope=scope,", source,
                         "an empty scope must not be reported as a complete incremental review")
        self.assertIn("EMPTY_WITH_MOVED_HEAD", source)

    def test_the_request_can_carry_the_compare(self) -> None:
        request = ReviewRequest(
            repo="o/r", pr=1, head_sha="abc", diff=None,
            compare={"status": "diverged", "files": []},
        )
        self.assertEqual(request.compare["status"], "diverged")

    def test_the_status_can_be_passed_alone(self) -> None:
        request = ReviewRequest(repo="o/r", pr=1, head_sha="abc", diff=None,
                                compare_status="ahead")
        self.assertEqual(request.compare_status, "ahead")


if __name__ == "__main__":
    unittest.main()
