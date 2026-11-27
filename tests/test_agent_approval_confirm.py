"""D16b: agent approval needs a second, deliberate step.

`allow_agent_approval: true` was one boolean. One line turned on a mode where an agent posts approvals on a
human's behalf, and if the rules were loosened afterwards the setting stayed on — now applying to paths it
was never confirmed for.

The required tests, per the spec: the flag alone leaves it off and says why; a confirmation turns it on; a
change to approvals.yml turns it off again; and there is no way to skip the confirmation.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest

from code_shipper.approvals.confirm import (
    Acknowledgement,
    ConfirmationRefused,
    agent_approval_is_enabled,
    approvals_hash,
    confirm_interactively,
    saved_ack,
)

RULES = """require:
  - path: src/**
    approvals: 2
"""

LOOSER = RULES + """  - path: /**
    approvals: 0
"""


class FakeTTY(io.StringIO):
    """A stream that claims to be a terminal, so the interactive path can be driven."""

    def isatty(self) -> bool:  # noqa: D102 - test double
        return True


def confirm(root: str, *, repo: str = "o/n", rules: str = RULES, typed: str | None = None):
    return confirm_interactively(
        repo=repo,
        approvals_yml=rules,
        state_dir=root,
        stdin=FakeTTY((typed if typed is not None else repo) + "\n"),
        stdout=io.StringIO(),
    )


class TheFlagAloneIsNotEnoughTests(unittest.TestCase):
    def test_with_no_confirmation_agent_approval_is_off_and_says_why(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            enabled, reason = agent_approval_is_enabled(state_dir=root, repo="o/n", approvals_yml=RULES)
            self.assertFalse(enabled)
            self.assertIn("not enabled", reason)
            # The reason must name the action that fixes it.
            self.assertIn("confirm-agent-approval", reason)

    def test_a_confirmation_turns_it_on(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            confirm(root)
            enabled, reason = agent_approval_is_enabled(state_dir=root, repo="o/n", approvals_yml=RULES)
            self.assertTrue(enabled, reason)

    def test_the_record_carries_the_repo_and_the_rules_hash(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            ack = confirm(root)
            self.assertEqual(ack.repo, "o/n")
            self.assertEqual(ack.approvals_sha, approvals_hash(RULES))
            self.assertIn("src/**", ack.rules_summary)


class EditingTheRulesInvalidatesTheConfirmationTests(unittest.TestCase):
    def test_tightening_or_loosening_the_rules_turns_it_off(self) -> None:
        """The whole point: loosening the rules later cannot inherit the old consent."""
        with tempfile.TemporaryDirectory() as root:
            confirm(root)
            enabled, _ = agent_approval_is_enabled(state_dir=root, repo="o/n", approvals_yml=RULES)
            self.assertTrue(enabled)

            enabled, reason = agent_approval_is_enabled(state_dir=root, repo="o/n", approvals_yml=LOOSER)
            self.assertFalse(enabled)
            self.assertIn("changed since", reason)

    def test_reformatting_without_changing_a_rule_does_not_invalidate(self) -> None:
        """A reflowed file that says the same thing must not demand a fresh confirmation."""
        reformatted = "require:\n  - path: src/**   \n    approvals: 2\n\n\n"
        self.assertEqual(approvals_hash(reformatted), approvals_hash(RULES))

    def test_a_changed_count_invalidates(self) -> None:
        self.assertNotEqual(approvals_hash(RULES.replace("approvals: 2", "approvals: 1")),
                            approvals_hash(RULES))

    def test_a_changed_path_invalidates(self) -> None:
        self.assertNotEqual(approvals_hash(RULES.replace("src/**", "src/**/*.py")),
                            approvals_hash(RULES))


class TheConfirmationCannotBeSkippedTests(unittest.TestCase):
    def test_the_wrong_word_cancels_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ConfirmationRefused) as caught:
                confirm(root, typed="o/n2")
            self.assertIn("Nothing was written", str(caught.exception))
            self.assertIsNone(saved_ack(root, "o/n"))

    def test_an_empty_answer_cancels(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ConfirmationRefused):
                confirm(root, typed="")
            self.assertIsNone(saved_ack(root, "o/n"))

    def test_with_no_terminal_it_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ConfirmationRefused) as caught:
                confirm_interactively(repo="o/n", approvals_yml=RULES, state_dir=root,
                                      stdin=io.StringIO("o/n\n"), stdout=io.StringIO())
            self.assertIn("no terminal", str(caught.exception))

    def test_there_is_no_yes_option_anywhere(self) -> None:
        """A skip flag would defeat the step, so the parser must not offer one."""
        from code_shipper import cli

        parser = cli.build_parser()
        for action in parser._actions:
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict) and "config" in choices:
                config = choices["config"]
                options = {opt for a in config._actions for opt in a.option_strings}
                self.assertNotIn("--yes", options)
                self.assertNotIn("--force", options)
                self.assertNotIn("--skip", options)

    def test_the_confirmation_prints_what_it_enables(self) -> None:
        """A confirmation that does not say what it turns on is a rubber stamp."""
        with tempfile.TemporaryDirectory() as root:
            out = io.StringIO()
            confirm_interactively(repo="o/n", approvals_yml=RULES, state_dir=root,
                                  stdin=FakeTTY("o/n\n"), stdout=out)
            printed = out.getvalue()
            self.assertIn("src/**", printed)
            self.assertIn("bound to the current approvals.yml", printed)


class TheRecordIsScopedToOneRepositoryTests(unittest.TestCase):
    def test_a_confirmation_does_not_transfer_to_another_repo(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            confirm(root, repo="o/n")
            enabled, reason = agent_approval_is_enabled(state_dir=root, repo="o/other",
                                                        approvals_yml=RULES)
            self.assertFalse(enabled)
            self.assertIn("not enabled", reason)

    def test_the_record_file_is_not_world_readable(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            confirm(root)
            path = os.path.join(root, "agent-approval", "o__n.json")
            mode = os.stat(path).st_mode & 0o777
            self.assertEqual(mode, 0o600, f"record is {oct(mode)}")

    def test_a_corrupt_record_reads_as_absent_rather_than_raising(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "agent-approval", "o__n.json")
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as handle:
                handle.write("{not json")
            self.assertIsNone(saved_ack(root, "o/n"))
            enabled, _ = agent_approval_is_enabled(state_dir=root, repo="o/n", approvals_yml=RULES)
            self.assertFalse(enabled, "a corrupt record must fail closed")


class TheGateIsWhereTheDecisionIsMadeTests(unittest.TestCase):
    """A record nothing consults would be theatre. The check lives in the evaluator, not a caller."""

    def _policy(self, root: str, rules: str = RULES):
        from code_shipper.approvals.policy import Policy

        return Policy(allow_agent_approval=True, agents={"mode": "separated"},
                      repo="o/n", state_dir=root, approvals_yml=rules)

    def test_the_flag_alone_is_refused_at_the_evaluator(self) -> None:
        from code_shipper.approvals.evaluate import refuses_agent_approval

        with tempfile.TemporaryDirectory() as root:
            blocked, reason = refuses_agent_approval(self._policy(root))
            self.assertTrue(blocked)
            self.assertIn("confirm-agent-approval", reason)

    def test_a_confirmed_repository_is_allowed(self) -> None:
        from code_shipper.approvals.evaluate import refuses_agent_approval

        with tempfile.TemporaryDirectory() as root:
            confirm(root)
            blocked, reason = refuses_agent_approval(self._policy(root))
            self.assertFalse(blocked, reason)

    def test_loosening_the_rules_blocks_it_again(self) -> None:
        from code_shipper.approvals.evaluate import refuses_agent_approval

        with tempfile.TemporaryDirectory() as root:
            confirm(root)
            blocked, reason = refuses_agent_approval(self._policy(root, LOOSER))
            self.assertTrue(blocked)
            self.assertIn("changed since", reason)

    def test_a_policy_with_no_state_dir_fails_closed(self) -> None:
        """Unverifiable consent is not consent."""
        from code_shipper.approvals.evaluate import refuses_agent_approval
        from code_shipper.approvals.policy import Policy

        blocked, reason = refuses_agent_approval(
            Policy(allow_agent_approval=True, agents={"mode": "separated"}, repo="o/n")
        )
        self.assertTrue(blocked)
        self.assertIn("cannot be checked", reason)


class TheRecordRoundTripsTests(unittest.TestCase):
    def test_json_round_trip(self) -> None:
        ack = Acknowledgement(repo="o/n", approvals_sha=approvals_hash(RULES), rules_summary="x")
        self.assertEqual(Acknowledgement.from_json(ack.to_json()), ack)


if __name__ == "__main__":
    unittest.main()
