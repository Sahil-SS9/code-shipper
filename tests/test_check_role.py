"""D13: the check-run role, tested separately as the instruction requires.

The correction that drove this file: **on GitHub a `neutral` conclusion satisfies a required check.** So
`neutral` is not a neutral choice for a review tool — it is a pass. Limits 6 and 8 of the proposal would
have let an incomplete review and an observe-only review pass a required check. Both are fixed:

* an incomplete review maps to `action_required`, never `neutral` or `success`;
* observe mode writes **no check at all**.

And the test the instruction asked for: **no non-complete state ever produces `success` or `neutral`.**
It is written as an exhaustive sweep over every projection state × complete/not-complete, so a new state
cannot slip through.

This file exists separately from the review suite because a check write can satisfy a required-status
rule. It deserves its own attack tests rather than being folded into the ordinary ones.
"""

from __future__ import annotations

import inspect
import pathlib
import unittest

from code_shipper.broker.roles import (
    FORBIDDEN_OPS,
    ROLE_OPS,
    Broker,
    BrokerRefused,
    CountingTransport,
)
from code_shipper.review import check as check_mod
from code_shipper.review.check import (
    ALLOWED_CONCLUSIONS,
    CHECK_NAME,
    NO_CHECK_STATES,
    STATE_CONCLUSIONS,
    CheckRefused,
    CheckRequest,
    assert_not_a_pass,
    assert_sha_is_head,
    conclusion_for,
)
from code_shipper.surfaces import fixtures

ALL_PROJECTION_STATES = tuple(STATE_CONCLUSIONS)


def broker(ledger_path=None) -> tuple[Broker, CountingTransport]:
    transport = CountingTransport()
    instance = Broker(token="fake", repo="o/r", transport=transport,
                      allowed_repos=frozenset({"o/r"}), ledger_path=ledger_path)
    return instance, transport


class ConclusionVocabularyTests(unittest.TestCase):
    """Limit 1: no conclusion shaped like an approval, and neutral is not available."""

    def test_neutral_is_not_in_the_vocabulary(self) -> None:
        self.assertNotIn("neutral", ALLOWED_CONCLUSIONS,
                         "on GitHub a neutral conclusion satisfies a required check")

    def test_approved_is_not_in_the_vocabulary(self) -> None:
        for word in ("approved", "approve", "pass", "lgtm"):
            self.assertNotIn(word, ALLOWED_CONCLUSIONS)

    def test_only_three_conclusions_exist(self) -> None:
        self.assertEqual(ALLOWED_CONCLUSIONS, frozenset({"success", "failure", "action_required"}))

    def test_no_state_maps_to_an_approval_word(self) -> None:
        for state, conclusion in STATE_CONCLUSIONS.items():
            if conclusion is None:
                continue
            self.assertIn(conclusion, ALLOWED_CONCLUSIONS, f"{state} maps to {conclusion}")


class IncompleteIsNeverAPassTests(unittest.TestCase):
    """Limits 6 and 8, corrected. The test the instruction asked for."""

    def test_an_incomplete_review_is_action_required(self) -> None:
        self.assertEqual(conclusion_for("incomplete", complete=False), "action_required")

    def test_incomplete_wins_over_a_green_looking_state(self) -> None:
        # `complete=False` outranks whatever the state summary says.
        self.assertEqual(conclusion_for("approvals-satisfied", complete=False), "action_required")

    def test_observe_writes_no_check(self) -> None:
        self.assertIsNone(conclusion_for("observe", complete=True))

    def test_empty_writes_no_check(self) -> None:
        self.assertIsNone(conclusion_for("empty", complete=True))

    def test_no_non_complete_state_ever_produces_success_or_neutral(self) -> None:
        """The exhaustive sweep: every state, not complete, must not be a pass."""
        produced: list[tuple[str, str]] = []
        for state in ALL_PROJECTION_STATES:
            conclusion = conclusion_for(state, complete=False)
            if conclusion in ("success", "neutral"):
                produced.append((state, conclusion))
            if conclusion is not None:
                self.assertEqual(
                    conclusion, "action_required",
                    f"{state} incomplete produced {conclusion}; expected action_required or no check",
                )
        self.assertEqual(produced, [], f"non-complete states produced a passing conclusion: {produced}")

    def test_no_complete_state_produces_neutral(self) -> None:
        for state in ALL_PROJECTION_STATES:
            self.assertNotEqual(conclusion_for(state, complete=True), "neutral",
                                f"{state} complete produced neutral, which GitHub treats as a pass")

    def test_every_state_has_an_explicit_mapping(self) -> None:
        """A new state must be mapped deliberately, not inherit a green default."""
        from code_shipper.surfaces.projection import STATES

        for state in STATES:
            self.assertIn(state, STATE_CONCLUSIONS,
                          f"projection state {state!r} has no check mapping")

    def test_an_unknown_state_is_refused_not_defaulted(self) -> None:
        with self.assertRaises(CheckRefused) as caught:
            conclusion_for("a-state-that-does-not-exist", complete=True)
        self.assertEqual(caught.exception.code, "unknown_state")


class StructuralGuardTests(unittest.TestCase):
    def test_a_passing_conclusion_for_an_incomplete_review_is_refused(self) -> None:
        with self.assertRaises(CheckRefused):
            assert_not_a_pass("success", complete=False, state="findings")

    def test_neutral_for_an_incomplete_review_is_refused(self) -> None:
        with self.assertRaises(CheckRefused):
            assert_not_a_pass("neutral", complete=False, state="incomplete")

    def test_a_check_for_observe_is_refused(self) -> None:
        with self.assertRaises(CheckRefused):
            assert_not_a_pass("success", complete=True, state="observe")

    def test_action_required_is_allowed_for_an_incomplete_review(self) -> None:
        assert_not_a_pass("action_required", complete=False, state="incomplete")


class PayloadTests(unittest.TestCase):
    def test_the_check_name_is_fixed(self) -> None:
        self.assertEqual(CHECK_NAME, "code-shipper/review")
        source = inspect.getsource(check_mod)
        self.assertIn('CHECK_NAME = "code-shipper/review"', source)

    def test_the_payload_carries_the_fixed_name(self) -> None:
        payload = CheckRequest(repo="o/r", head_sha="abc", state="findings",
                               complete=True).as_payload()
        assert payload is not None
        self.assertEqual(payload["name"], CHECK_NAME)

    def test_a_caller_cannot_supply_a_name(self) -> None:
        fields = CheckRequest.__dataclass_fields__
        self.assertNotIn("name", fields, "a caller-supplied name could impersonate another gate")

    def test_observe_produces_no_payload(self) -> None:
        self.assertIsNone(CheckRequest(repo="o/r", head_sha="abc", state="observe",
                                       complete=True).as_payload())

    def test_a_title_claiming_approval_is_refused(self) -> None:
        for title in ("Approved", "Review approved", "OK to merge", "LGTM"):
            with self.assertRaises(CheckRefused) as caught:
                CheckRequest(repo="o/r", head_sha="abc", state="approvals-satisfied",
                             complete=True, title=title).as_payload()
            self.assertEqual(caught.exception.code, "approval_language")

    def test_a_normal_title_is_accepted(self) -> None:
        payload = CheckRequest(repo="o/r", head_sha="abc", state="findings", complete=True,
                               title="Review: 2 important findings").as_payload()
        assert payload is not None
        self.assertEqual(payload["output"]["title"], "Review: 2 important findings")

    def test_the_payload_status_is_completed(self) -> None:
        payload = CheckRequest(repo="o/r", head_sha="abc", state="findings",
                               complete=True).as_payload()
        assert payload is not None
        self.assertEqual(payload["status"], "completed")


class ExactHeadTests(unittest.TestCase):
    """Limit 4."""

    def test_a_mismatched_sha_is_refused(self) -> None:
        with self.assertRaises(CheckRefused) as caught:
            assert_sha_is_head(requested="aaa", observed_head="bbb")
        self.assertEqual(caught.exception.code, "not_the_head")

    def test_a_matching_sha_is_allowed(self) -> None:
        assert_sha_is_head(requested="aaa", observed_head="aaa")

    def test_a_missing_sha_is_refused(self) -> None:
        with self.assertRaises(CheckRefused):
            assert_sha_is_head(requested="", observed_head="aaa")

    def test_a_missing_observed_head_is_refused(self) -> None:
        with self.assertRaises(CheckRefused):
            assert_sha_is_head(requested="aaa", observed_head="")


class RoleTests(unittest.TestCase):
    """Limit 5: only `reporter` may write a check, and refusals send nothing."""

    def test_write_check_run_is_no_longer_forbidden(self) -> None:
        self.assertNotIn("write_check_run", FORBIDDEN_OPS)

    def test_the_destructive_operations_stay_forbidden(self) -> None:
        for op in ("merge", "force_push", "delete_branch", "push_tag"):
            self.assertIn(op, FORBIDDEN_OPS, f"{op} must stay forbidden")

    def test_only_reporter_has_the_operation(self) -> None:
        holders = [role for role, ops in ROLE_OPS.items() if "write_check_run" in ops]
        self.assertEqual(holders, ["reporter"],
                         "reviewer and approver must not be able to gate a merge")

    def test_the_reviewer_cannot_write_a_check(self) -> None:
        instance, transport = broker()
        with self.assertRaises(BrokerRefused) as caught:
            instance.write_check(pr=1, head_sha="abc", observed_head="abc", state="findings",
                                 complete=True, role="reviewer")
        self.assertIn(caught.exception.code, ("role_may_not", "not_the_head"))
        self.assertEqual(transport.count, 0, "a refusal must not send anything")

    def test_the_approver_cannot_write_a_check(self) -> None:
        instance, transport = broker()
        with self.assertRaises(BrokerRefused):
            instance.write_check(pr=1, head_sha="abc", observed_head="abc", state="findings",
                                 complete=True, role="approver")
        self.assertEqual(transport.count, 0)

    def test_the_user_role_cannot_write_a_check(self) -> None:
        instance, transport = broker()
        with self.assertRaises(BrokerRefused):
            instance.write_check(pr=1, head_sha="abc", observed_head="abc", state="findings",
                                 complete=True, role="user")
        self.assertEqual(transport.count, 0)

    def test_the_reporter_can_write_a_check(self) -> None:
        instance, transport = broker()
        instance.write_check(pr=1, head_sha="abc", observed_head="abc", state="findings",
                            complete=True)
        self.assertEqual(transport.count, 1)

    def test_a_mismatched_head_is_refused_before_any_request(self) -> None:
        instance, transport = broker()
        with self.assertRaises(BrokerRefused):
            instance.write_check(pr=1, head_sha="aaa", observed_head="bbb", state="findings",
                                 complete=True)
        self.assertEqual(transport.count, 0, "the head check happens before the request")

    def test_observe_sends_nothing_even_for_the_reporter(self) -> None:
        instance, transport = broker()
        result = instance.write_check(pr=1, head_sha="abc", observed_head="abc", state="observe",
                                     complete=True)
        self.assertIsNone(result)
        self.assertEqual(transport.count, 0, "observe mode must write no check at all")

    def test_the_payload_goes_to_the_check_runs_endpoint(self) -> None:
        instance, transport = broker()
        instance.write_check(pr=1, head_sha="abc", observed_head="abc", state="findings",
                            complete=True)
        url = transport.requests[0][0]
        self.assertIn("/check-runs", url)

    def test_the_write_is_ledgered(self) -> None:
        """Limit 9: who set this gate must be answerable later."""
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "ledger.jsonl"
            instance, transport = broker(ledger_path=path)
            instance.write_check(pr=1, head_sha="abc", observed_head="abc", state="findings",
                                 complete=True)
            rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        self.assertTrue(rows, "every check write must leave a ledger row")
        self.assertEqual(rows[-1].get("op"), "write_check_run")
        self.assertEqual(rows[-1].get("role"), "reporter")
        self.assertEqual(rows[-1].get("expected_head"), "abc")


class FixtureConsistencyTests(unittest.TestCase):
    """The ten review states must produce the conclusions the report claims."""

    def test_each_fixture_state_maps_as_documented(self) -> None:
        expected = {
            "findings": "failure",
            "incomplete": "action_required",
            "approvals-satisfied": "success",
            "approvals-blocked": "success",
            "observe": None,
            "paused": "action_required",
            "stopped": "action_required",
            "error": "failure",
            "reviewing": "action_required",
            "empty": None,
        }
        for name, wanted in expected.items():
            self.assertEqual(conclusion_for(name, complete=True), wanted, name)


if __name__ == "__main__":
    unittest.main()


class SuccessTitleTests(unittest.TestCase):
    """Fix 6: a green tick must not read as ready.

    `approvals-blocked` correctly concludes `success` — the review itself is clear — but a title that
    omits the outstanding approvals turns a green check into a claim that nothing is left to do.
    """

    def _title(self, **kwargs) -> str:
        payload = CheckRequest(repo="o/r", head_sha="abc", complete=True, **kwargs).as_payload()
        assert payload is not None
        return payload["output"]["title"]

    def test_every_success_title_in_approvals_blocked_names_the_outstanding_approvals(self) -> None:
        for have, needed in ((0, 2), (1, 3), (0, 1)):
            title = self._title(state="approvals-blocked", approvals_have=have,
                                approvals_needed=needed)
            self.assertIn("approvals still needed", title.lower())
            self.assertIn(f"({have} of {needed})", title)

    def test_the_exact_required_title(self) -> None:
        self.assertEqual(
            self._title(state="approvals-blocked", approvals_have=0, approvals_needed=2),
            "Review clear · approvals still needed (0 of 2)",
        )

    def test_without_counts_it_still_says_approvals_are_needed(self) -> None:
        title = self._title(state="approvals-blocked")
        self.assertIn("approvals still needed", title.lower())
        self.assertNotIn("0 of 0", title)

    def test_no_success_title_omits_the_outstanding_approvals_in_that_state(self) -> None:
        """Swept over plausible counts: every one must name them."""
        for needed in range(1, 6):
            for have in range(0, needed):
                title = self._title(state="approvals-blocked", approvals_have=have,
                                    approvals_needed=needed)
                self.assertIn("approvals", title.lower())
                self.assertNotEqual(title.strip().lower(), "review complete")

    def test_the_ready_state_says_rules_are_met(self) -> None:
        title = self._title(state="approvals-satisfied")
        self.assertIn("rules are met", title.lower())

    def test_a_success_title_never_claims_approval(self) -> None:
        """`approvals` is the word we want; `approved` and `approve` are the claims we refuse."""
        for state in ("approvals-blocked", "approvals-satisfied"):
            title = self._title(state=state).lower()
            for claim in ("approved", "approve ", "approve,", "approved."):
                self.assertNotIn(claim, title)
            self.assertFalse(title.rstrip().endswith("approve"), title)

    def test_an_incomplete_title_says_it_is_not_a_pass(self) -> None:
        payload = CheckRequest(repo="o/r", head_sha="abc", state="incomplete",
                               complete=False).as_payload()
        assert payload is not None
        self.assertIn("not a pass", payload["output"]["title"].lower())
