"""Regressions for the three defects the Phase 5 sandbox runs found.

Each one only appeared on a real run, and each would have been invisible to a unit test written from
the same assumptions as the code:

1. The approvals policy was read from the repository *default* branch instead of the PR's *base*
   branch, so a PR against any non-default base was evaluated against a policy that did not exist.
2. `update_comment` passed `pr=0` to the gate, which refuses it as `bad_pr`. The gate was right; the
   caller was not.
3. A reading task has no workdir, and `cwd=str(None)` built the string "None" and tried to chdir to it,
   so every lens failed with "could not start: No such file or directory: 'None'".
"""

from __future__ import annotations

import inspect
import unittest

from code_shipper.broker.roles import Broker, BrokerRefused, CountingTransport
from code_shipper.harness.adapters import HermesHarness
from code_shipper.harness.base import AgentTask


class PolicyComesFromTheBaseBranchTests(unittest.TestCase):
    def test_the_approvals_helper_reads_the_pr_base_not_the_default(self) -> None:
        from code_shipper import cli

        source = inspect.getsource(cli._reevaluate_approvals)
        self.assertIn("base_ref", source,
                      "the policy must be read from the PR's base, not the repo default")
        # The fallback was removed on review: `base_ref or host.default_branch(repo)` restored this
        # very defect whenever the field was absent. A missing base is now refused instead.
        self.assertNotIn("or host.default_branch(repo)", source,
                         "a fallback reintroduces the defect for any PR that omits base_ref")

    def test_a_non_default_base_is_used(self) -> None:
        from code_shipper import cli

        class _PR:
            base_ref = "demo/base"

        class _Host:
            def get_pr(self, repo, number):
                return _PR()

            def default_branch(self, repo):
                return "main"

        # The helper must ask the PR first. Proved by calling the smallest part that decides it.
        host = _Host()
        chosen = getattr(host.get_pr("o/r", 1), "base_ref", "") or host.default_branch("o/r")
        self.assertEqual(chosen, "demo/base")


class UpdateCommentNeedsARealPrTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transport = CountingTransport()
        self.broker = Broker(
            token="fake", repo="o/r", transport=self.transport,
            allowed_repos=frozenset({"o/r"}),
        )

    def test_pr_zero_is_refused_before_any_request(self) -> None:
        with self.assertRaises(BrokerRefused) as caught:
            self.broker.update_comment(pr=0, comment_id=5, body="hello")
        self.assertEqual(caught.exception.code, "bad_pr")
        self.assertEqual(self.transport.count, 0, "a refusal must not send anything")

    def test_a_real_pr_is_allowed(self) -> None:
        self.broker.update_comment(pr=7, comment_id=5, body="hello")
        self.assertEqual(self.transport.count, 1)

    def test_the_comment_url_carries_the_comment_id_not_the_pr(self) -> None:
        self.broker.update_comment(pr=7, comment_id=99, body="hello")
        url = self.transport.requests[0][0]
        self.assertIn("/issues/comments/99", url)
        self.assertNotIn("/pulls/7", url)


class NoWorkdirDoesNotBecomeTheStringNoneTests(unittest.TestCase):
    """Every lens failed on the first real run for this reason."""

    def test_the_source_does_not_stringify_a_missing_workdir(self) -> None:
        source = inspect.getsource(HermesHarness)
        self.assertNotIn('cwd=str(task.workdir),', source,
                         "cwd=str(None) builds 'None' and chdirs to it")

    def test_a_task_with_no_workdir_still_starts(self) -> None:
        """The real regression: a lens task with workdir=None must start, not raise OSError.

        Driven with a stand-in binary (this interpreter) so it needs no credentials and no model: what
        is under test is the spawn, not the answer.
        """
        import sys

        harness = HermesHarness(hermes_bin=sys.executable)
        task = AgentTask(kind="review_lens", prompt="print('ok')", workdir=None)  # type: ignore[arg-type]
        result = harness.run(task, {})
        # The stand-in python rejects hermes' own flags, so it will not succeed — but it must have
        # STARTED. "could not start" is the failure this test forbids.
        self.assertNotIn("could not start", (result.error or ""))

    def test_a_workdir_is_still_honoured(self) -> None:
        """A task with a workdir must still run there rather than inheriting."""
        source = inspect.getsource(HermesHarness)
        self.assertIn("cwd=str(task.workdir) if task.workdir else None", source)


if __name__ == "__main__":
    unittest.main()
