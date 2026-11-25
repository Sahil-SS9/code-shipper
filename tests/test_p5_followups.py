"""The two Phase 5 follow-up fixes.

**Fix 1: an unsatisfiable rule must be refused.** `count: 2` with one named approver parsed happily, so
PR #25's comment said "2 more approval(s) needed" — a statement that was false, because no number of
approvals could ever reach it. A rule nothing can satisfy is worse than no rule: it reads as a gate and
behaves as a permanent block with a misleading explanation.

**Fix 2: a missing base must be refused, and the policy read at a fixed SHA.** `base_ref or
default_branch(repo)` silently restored the exact defect the previous stop found, for any PR whose
payload omitted the field. A fallback that reintroduces a fixed bug under slightly different input is
not a fallback. And a branch *name* is not a fixed reference: reading "the branch" can return a policy
that changed after the head being evaluated, so the verdict and the code it describes can drift.
"""

from __future__ import annotations

import inspect
import unittest

from code_shipper.approvals.policy import PolicyError, parse


class UnsatisfiableRuleTests(unittest.TestCase):
    """Fix 1."""

    def test_count_two_with_one_approver_is_refused(self) -> None:
        # the exact shape PR #25 carried
        with self.assertRaises(PolicyError) as caught:
            parse("version: 1\napprovals:\n  - count: 2\n    approvers: [Sahil-SS9]\n")
        message = str(caught.exception)
        self.assertIn("2", message)
        self.assertIn("1", message, "both numbers must be stated")

    def test_count_five_with_two_approvers_is_refused(self) -> None:
        with self.assertRaises(PolicyError) as caught:
            parse("version: 1\napprovals:\n  - count: 5\n    approvers: [a, b]\n")
        message = str(caught.exception)
        self.assertIn("5", message)
        self.assertIn("2", message)

    def test_the_message_says_it_can_never_be_satisfied(self) -> None:
        with self.assertRaises(PolicyError) as caught:
            parse("version: 1\napprovals:\n  - count: 3\n    approvers: [a]\n")
        self.assertIn("can never be satisfied", str(caught.exception))

    def test_the_message_says_what_to_change(self) -> None:
        with self.assertRaises(PolicyError) as caught:
            parse("version: 1\napprovals:\n  - count: 3\n    approvers: [a]\n")
        self.assertIn("name more approvers", str(caught.exception))

    def test_count_equal_to_the_approvers_is_accepted(self) -> None:
        policy = parse("version: 1\napprovals:\n  - count: 2\n    approvers: [a, b]\n")
        self.assertEqual(policy.rules[0].count, 2)

    def test_count_below_the_approvers_is_accepted(self) -> None:
        policy = parse("version: 1\napprovals:\n  - count: 1\n    approvers: [a, b, c]\n")
        self.assertEqual(policy.rules[0].count, 1)

    def test_a_satisfiable_rule_still_blocks_until_satisfied(self) -> None:
        from code_shipper.approvals.evaluate import Facts, evaluate

        policy = parse("version: 1\napprovals:\n  - count: 2\n    approvers: [a, b]\n")
        self.assertFalse(evaluate(policy, Facts(approvals=("a",))).satisfied)
        self.assertTrue(evaluate(policy, Facts(approvals=("a", "b"))).satisfied)

    def test_seats_still_cannot_exceed_the_approvers(self) -> None:
        with self.assertRaises(PolicyError):
            parse("version: 1\napprovals:\n  - count: 2\n    approvers: [a, b]\n    seats: 3\n")


class BaseMustBeExplicitTests(unittest.TestCase):
    """Fix 2: no silent fallback, and a fixed reference."""

    def test_the_helper_has_no_default_branch_fallback(self) -> None:
        from code_shipper import cli

        source = inspect.getsource(cli._reevaluate_approvals)
        self.assertNotIn("or host.default_branch(repo)", source,
                         "a fallback restores the defect for any PR that omits base_ref")

    def test_the_helper_reads_at_the_base_sha_not_the_branch_name(self) -> None:
        from code_shipper import cli

        source = inspect.getsource(cli._reevaluate_approvals)
        self.assertIn("base_sha", source)
        # the branch name must not be the reference passed to read_file
        self.assertIn('host.read_file(repo, base_sha,', source)
        self.assertNotIn('host.read_file(repo, base_ref,', source)

    def test_a_missing_base_ref_is_refused(self) -> None:
        from code_shipper import cli

        class _PR:
            base_ref = ""
            base_sha = "abc123"

        class _Host:
            def get_pr(self, repo, number):
                return _PR()

            def default_branch(self, repo):
                raise AssertionError("the default branch must not be consulted")

        with self.assertRaises(cli.ApprovalRefused) as caught:
            cli._reevaluate_approvals(host=_Host(), repo="o/r", pr=1, token="fake")
        self.assertIn("no base branch", str(caught.exception))

    def test_a_missing_base_sha_is_refused(self) -> None:
        from code_shipper import cli

        class _PR:
            base_ref = "demo/base"
            base_sha = ""

        class _Host:
            def get_pr(self, repo, number):
                return _PR()

        with self.assertRaises(cli.ApprovalRefused) as caught:
            cli._reevaluate_approvals(host=_Host(), repo="o/r", pr=1, token="fake")
        self.assertIn("no base commit", str(caught.exception))

    def test_the_policy_fetch_uses_the_sha(self) -> None:
        """End to end: the reference handed to read_file is the base SHA, not the branch name."""
        from code_shipper import cli

        asked: list[tuple[str, str]] = []

        class _PR:
            base_ref = "demo/base"
            base_sha = "deadbeefcafe"
            title = "t"
            author = "a"
            draft = False
            labels = ()

        class _Host:
            def get_pr(self, repo, number):
                return _PR()

            def read_file(self, repo, ref, path):
                asked.append((ref, path))
                return None

            def get_diff(self, repo, number):
                from code_shipper.hosts.base import Diff
                return Diff(files=())

            def list_comments(self, repo, number):
                return []

            def list_reviews(self, repo, number):
                return []

        try:
            cli._reevaluate_approvals(host=_Host(), repo="o/r", pr=1, token="fake", dry_run=True)
        except Exception:  # noqa: BLE001 - the merge/evaluate path may go further; the fetch is what matters
            pass

        self.assertTrue(asked, "the policy was never fetched")
        for ref, _path in asked:
            self.assertEqual(ref, "deadbeefcafe",
                             "the policy must be read at the base SHA, not a branch name")

    def test_the_branch_is_resolved_to_its_tip_not_the_merge_base(self) -> None:
        """`pr.base.sha` is the merge base, not the tip: measured 2e9e11ad against a tip of 514cc54b.

        Reading the policy at the merge base applies a stale policy — on PR #25 the tip's policy had
        already changed from an unsatisfiable rule to a valid one, so the merge base would have
        reported an error about a policy that no longer existed.
        """
        from code_shipper import cli

        asked: list[str] = []

        class _PR:
            base_ref = "demo/base"
            base_sha = "merge-base-sha"
            title = "t"
            author = "a"
            draft = False
            labels = ()

        class _Host:
            def get_pr(self, repo, number):
                return _PR()

            def resolve_ref(self, repo, ref):
                return "branch-tip-sha"

            def read_file(self, repo, ref, path):
                asked.append(ref)
                return None

            def get_diff(self, repo, number):
                from code_shipper.hosts.base import Diff
                return Diff(files=())

            def list_comments(self, repo, number):
                return []

            def list_reviews(self, repo, number):
                return []

        try:
            cli._reevaluate_approvals(host=_Host(), repo="o/r", pr=1, token="fake", dry_run=True)
        except Exception:  # noqa: BLE001 - the fetch reference is what is under test
            pass

        self.assertTrue(asked)
        for ref in asked:
            self.assertEqual(ref, "branch-tip-sha",
                             "the policy must be read at the branch tip, not the merge base")

    def test_the_pr_type_carries_base_sha(self) -> None:
        from code_shipper.hosts.base import PullRequest

        self.assertIn("base_sha", PullRequest.__dataclass_fields__)

    def test_the_github_host_parses_base_sha_from_the_payload(self) -> None:
        import inspect as _inspect

        from code_shipper.hosts import github

        source = _inspect.getsource(github)
        self.assertIn('base_sha=str(base.get("sha", ""))', source)


if __name__ == "__main__":
    unittest.main()
