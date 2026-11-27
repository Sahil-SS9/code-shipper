"""Choose the harness and profile, or be told why you cannot.

The README promised "with any agent harness" while `HermesHarness()` was hard-coded at three call sites, so
no command could select anything else. One factory now decides, refuses before any work starts, and every
summary names the harness, profile and model that actually ran.
"""

from __future__ import annotations

import ast
import os
import inspect
import sys
import unittest
from pathlib import Path

from code_shipper.harness.adapters import CommandHarness, HermesHarness
from code_shipper.harness.base import AgentTask, Budget
from code_shipper.harness.factory import (
    DEFAULT_HARNESS,
    HARNESS_NAMES,
    UNRESOLVED_MODEL,
    HarnessRefused,
    _model_for,
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


class RefusalComesBeforeAnyNetworkWorkTests(unittest.TestCase):
    def test_the_harness_is_resolved_before_the_pull_request_is_read(self) -> None:
        """Found by running it: `--harness claude` fetched the PR and reported a 404 about the repository.

        The spec says UNSUPPORTED is refused before any work starts. Ordering is the requirement, so it is
        asserted against the source rather than only through a side effect.
        """
        source = CLI.read_text()
        body = source[source.index("def cmd_review("):]
        body = body[:body.index("def ", 10)]
        harness_at = body.index("build_harness")
        fetch_at = body.index("host.get_diff")
        self.assertLess(harness_at, fetch_at,
                        "the harness must be resolved before the PR is fetched")

    def test_a_refused_harness_reports_the_harness_and_not_the_repository(self) -> None:
        """The symptom the user saw was a 404 about the repo, which hid the real problem."""
        source = CLI.read_text()
        body = source[source.index("def cmd_review("):]
        body = body[:body.index("def ", 10)]
        self.assertLess(body.index("build_harness"), body.index("read_token"))


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


class TheProfileFlagIsNotANoOpTests(unittest.TestCase):
    """`--hermes-profile` set HERMES_PROFILE and stopped there, which Hermes does not read.

    Hermes picks a profile from `-p/--profile` via `scan_profile_flag` in main._apply_profile_override;
    HERMES_PROFILE is only a label kanban reads. So the flag was documented as choosing a profile while
    choosing nothing. These tests assert the two things Hermes actually uses: the argv token, and the
    resolved model that the summary reports.
    """

    def _task(self) -> AgentTask:
        return AgentTask(kind="review_lens", prompt="x", workdir=None,
                         budget=Budget(max_turns=1, max_seconds=60))

    def test_the_profile_reaches_the_argv_hermes_parses(self) -> None:
        selection = build_harness(name="hermes", profile="mrhermagi")
        argv = selection.harness.argv(self._task())
        self.assertIn("-p", argv, "the profile flag is absent from the argv Hermes reads")
        self.assertEqual(argv[argv.index("-p") + 1], "mrhermagi")

    def test_hermes_itself_reads_that_argv_as_the_profile(self) -> None:
        """Uses Hermes's own scanner, so this cannot drift from what Hermes does.

        `scan_profile_flag` is the function `_apply_profile_override` calls before argparse runs. Testing
        against it means the assertion is about Hermes's behaviour, not about our idea of it.
        """
        try:
            sys.path.insert(0, str(Path("/home/kensei/repos/KenseiAgent")))
            from hermes_cli._parser import scan_profile_flag
        except (ImportError, OSError):
            self.skipTest("the Hermes source is not available here")

        selection = build_harness(name="hermes", profile="mrhermagi")
        argv = selection.harness.argv(self._task())
        # Hermes scans sys.argv[1:], i.e. everything after the program name.
        name, consumed, index = scan_profile_flag(argv[1:])
        self.assertEqual(name, "mrhermagi")
        self.assertEqual(consumed, 2)
        self.assertEqual(argv[1:][index], "-p")

    def test_the_summary_names_the_profiles_model_not_a_placeholder(self) -> None:
        """The second half of the item: the banner said "(harness default)" for every run."""
        selection = build_harness(name="hermes", profile="mrhermagi")
        if selection.model.startswith(UNRESOLVED_MODEL):
            self.skipTest("profile mrhermagi is not visible on this machine: " + selection.model)
        self.assertIn("model: ", selection.banner())
        self.assertIn(selection.model, selection.banner())
        self.assertEqual(selection.summary_fields()["model"], selection.model)

    def test_two_profiles_report_different_models(self) -> None:
        """If the profile were a no-op, both would report the same model."""
        default = build_harness(name="hermes", profile="")
        other = build_harness(name="hermes", profile="mrhermagi")
        if other.model.startswith(UNRESOLVED_MODEL):
            self.skipTest("profiles are not resolvable in this environment")
        self.assertNotEqual(default.model, other.model,
                            "both profiles report the same model, so the profile chose nothing")

    def test_an_unresolvable_profile_says_why_in_one_line(self) -> None:
        """A throwaway HERMES_HOME with --hermes-profile cannot see the profile. Say so, not a placeholder."""
        from unittest import mock

        with mock.patch.dict(os.environ, {"HERMES_HOME": "/tmp/throwaway-home"}), \
                mock.patch("code_shipper.harness.factory._hermes_model", return_value=""):
            model = _model_for(object(), "mrhermagi")
        self.assertEqual(model, "(unknown: profile mrhermagi is not visible from HERMES_HOME /tmp/throwaway-home)")
        self.assertNotIn("\n", model)

    def test_the_banner_never_shows_an_empty_profile(self) -> None:
        self.assertIn("(default)", build_harness(name="hermes").banner())

    def test_a_non_hermes_harness_carries_no_profile(self) -> None:
        """openai_compat refuses here for want of credentials; that refusal must not be about the profile."""
        try:
            selection = build_harness(name="openai_compat", profile="mrhermagi")
        except HarnessRefused as exc:
            self.assertIn("openai_compat", str(exc))
            self.assertNotIn("profile", str(exc).lower())
            self.skipTest("openai_compat needs credentials in this environment")
        self.assertEqual(selection.profile, "")

    def test_an_unknown_profile_does_not_crash_the_review(self) -> None:
        """Resolution must degrade to unknown, not raise: a bad profile is Hermes's to refuse."""
        selection = build_harness(name="hermes", profile="nope-not-a-profile")
        self.assertEqual(selection.profile, "nope-not-a-profile")
        self.assertIsInstance(selection.model, str)
        self.assertTrue(selection.model)
