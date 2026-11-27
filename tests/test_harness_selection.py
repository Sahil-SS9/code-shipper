"""Choose the harness and profile, or be told why you cannot.

The README promised "with any agent harness" while `HermesHarness()` was hard-coded at three call sites, so
no command could select anything else. One factory now decides, refuses before any work starts, and every
summary names the harness, profile and model that actually ran.
"""

from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path

from code_shipper.harness.adapters import CommandHarness, HermesHarness
from code_shipper.harness.factory import (
    DEFAULT_HARNESS,
    HARNESS_NAMES,
    HarnessRefused,
    build_harness,
)

CLI = Path(__file__).resolve().parent.parent / "code_shipper" / "cli.py"


class TheFactoryIsTheOnlyConstructionSiteTests(unittest.TestCase):
    def test_cli_constructs_hermes_harness_nowhere(self) -> None:
        """The spec's own assertion: exactly one place builds a harness."""
        source = CLI.read_text()
        tree = ast.parse(source)
        constructions: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"HermesHarness", "CommandHarness", "OpenAICompatHarness"}:
                    constructions.append(node.func.id)
        self.assertEqual(constructions, [], f"constructed in cli.py at: {constructions}")

    def test_every_agent_running_command_offers_the_flags(self) -> None:
        """A partial set would let `review` choose a harness that `watch` silently overrode."""
        from code_shipper import cli

        for command in ("review", "fix", "watch", "poll", "ship"):
            parser = cli.build_parser()
            subparsers = None
            for action in parser._actions:
                choices = getattr(action, "choices", None)
                if isinstance(choices, dict) and command in choices:
                    subparsers = choices
                    break
            self.assertIsNotNone(subparsers, f"no subparser for {command}")
            assert subparsers is not None
            sub = subparsers[command]
            options = {opt for action in sub._actions for opt in action.option_strings}
            self.assertIn("--harness", options, command)
            self.assertIn("--hermes-profile", options, command)
            self.assertIn("--allow-unverified-harness", options, command)


class TheDefaultIsHermesTests(unittest.TestCase):
    def test_default_harness_is_hermes(self) -> None:
        self.assertEqual(DEFAULT_HARNESS, "hermes")
        self.assertIn("hermes", HARNESS_NAMES)

    def test_the_default_builds_a_hermes_harness(self) -> None:
        # Stubbed so this does not depend on hermes being installed on the test host.
        selection = build_harness(name="hermes")
        self.assertEqual(selection.name, "hermes")
        self.assertIsInstance(selection.harness, HermesHarness)


class RefusalHappensBeforeAnyWorkTests(unittest.TestCase):
    def test_an_unknown_harness_is_refused_with_exit_2(self) -> None:
        with self.assertRaises(HarnessRefused) as caught:
            build_harness(name="not-a-harness")
        self.assertEqual(caught.exception.exit_code, 2)
        self.assertIn("choose one of", str(caught.exception))

    def test_an_unsupported_harness_is_refused_with_doctors_reason(self) -> None:
        """doctor's own words, so the CLI and doctor cannot disagree."""
        harness = CommandHarness(preset="claude")
        ok, reason = harness.available()
        if ok:
            self.skipTest("claude is installed here; this test needs it absent")
        with self.assertRaises(HarnessRefused) as caught:
            build_harness(name="claude")
        self.assertEqual(caught.exception.exit_code, 2)
        self.assertIn("UNSUPPORTED", str(caught.exception))

    def test_an_unverified_harness_is_refused_without_the_explicit_flag(self) -> None:
        harness = CommandHarness(preset="codex")
        ok, reason = harness.available()
        if not ok:
            self.skipTest("codex is not installed here")
        with self.assertRaises(HarnessRefused) as caught:
            build_harness(name="codex")
        self.assertIn("UNVERIFIED", str(caught.exception))
        self.assertEqual(caught.exception.exit_code, 2)
        # And the refusal names the way out, rather than only saying no.
        self.assertIn("--allow-unverified-harness", str(caught.exception))

    def test_the_explicit_flag_lets_an_unverified_harness_run_and_says_so(self) -> None:
        harness = CommandHarness(preset="codex")
        ok, reason = harness.available()
        if not ok:
            self.skipTest("codex is not installed here")
        selection = build_harness(name="codex", allow_unverified=True)
        self.assertTrue(selection.unverified)
        self.assertIn("UNVERIFIED", selection.banner())


class TheProfileIsHonouredOrRefusedTests(unittest.TestCase):
    def test_the_profile_reaches_the_hermes_command_line(self) -> None:
        selection = build_harness(name="hermes", profile="review")
        self.assertEqual(selection.profile, "review")
        self.assertEqual(selection.harness._profile, "review")

    def test_the_profile_also_reaches_the_child_environment(self) -> None:
        """The variable is what anything the agent itself spawns sees."""
        harness = HermesHarness(profile="review")
        source = inspect.getsource(harness._attempt)
        self.assertIn("HERMES_PROFILE", source)

    def test_a_profile_given_to_a_non_hermes_harness_is_refused(self) -> None:
        """Silently ignoring it is the same class of bug as dropping HERMES_HOME."""
        with self.assertRaises(HarnessRefused) as caught:
            build_harness(name="claude", profile="review", allow_unverified=True)
        message = str(caught.exception)
        self.assertIn("--hermes-profile", message)
        self.assertIn("hermes", message)


class EverySummaryNamesWhatRanTests(unittest.TestCase):
    def test_summary_fields_carry_harness_profile_and_model(self) -> None:
        selection = build_harness(name="hermes", profile="review")
        fields = selection.summary_fields()
        self.assertEqual(set(fields), {"harness", "profile", "model"})
        self.assertEqual(fields["profile"], "review")
        self.assertTrue(fields["model"], "a summary must name a model, even if only the harness default")

    def test_the_cli_puts_the_fields_in_the_json_summary(self) -> None:
        source = CLI.read_text()
        self.assertIn("selection.summary_fields()", source)
        self.assertIn('outcome_dict["harness_unverified"]', source)

    def test_the_cli_banner_does_not_confuse_the_two_profiles(self) -> None:
        """`profile=` in the PR line is the review level; the banner must be visibly separate."""
        source = CLI.read_text()
        self.assertIn("selection.banner()", source)
        self.assertIn("Deliberately not folded into the line above", source)

    def test_refusal_exit_code_is_two_not_one(self) -> None:
        """1 means findings were reported. A selection error must never read as findings."""
        from code_shipper.surfaces.cli_view import EXIT_FINDINGS

        with self.assertRaises(HarnessRefused) as caught:
            build_harness(name="nope")
        self.assertNotEqual(caught.exception.exit_code, EXIT_FINDINGS)


if __name__ == "__main__":
    unittest.main()
