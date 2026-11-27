"""The first-review walkthrough exists, is ordered, and does not lie.

Kensei's fresh-install finding A1: the README stopped at `--help`. Nothing explained how to run a first
review, which AI it uses, what it costs, or that `--dry-run` comes first. A4: the status line said the TUI
and Desktop were awaiting sign-off when they had been signed off, and the platform table said 1,236 tests.

Every command in the walkthrough was run as written; the output quoted in it is real. These tests keep it
from rotting back into a stub.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"


class TheWalkthroughExistsAndIsOrderedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = README.read_text()

    def test_the_section_is_present(self) -> None:
        self.assertIn("## First review", self.text)

    def test_it_sits_after_install(self) -> None:
        self.assertLess(self.text.index("## Install"), self.text.index("## First review"))

    def test_it_is_short_enough_to_be_read(self) -> None:
        """The spec said about 40 lines; a wall of prose is the same failure as no section."""
        start = self.text.index("## First review")
        end = self.text.index("### Windows")
        body = self.text[start:end]
        # The spec said "about 40 lines". It is longer because it documents the cached-rerun and
        # exit-code semantics, which a reader hit as a contradiction when they were absent. The cap keeps
        # it from becoming a manual rather than enforcing the original estimate.
        self.assertLess(len(body.splitlines()), 105, "the walkthrough has grown into a manual")

    def test_dry_run_comes_before_posting(self) -> None:
        """The order is the safety property: a stranger must not post on their first command."""
        dry = self.text.index("--dry-run")
        watch = self.text.index("code_shipper.cli watch")
        self.assertLess(dry, watch)

    def test_it_says_which_ai_and_that_it_costs_money(self) -> None:
        self.assertIn("Which AI it uses", self.text)
        self.assertIn("costs whatever that model costs", self.text)

    def test_it_states_the_exit_codes(self) -> None:
        for code in ("**0**", "**1**", "**2**", "**3**"):
            self.assertIn(code, self.text)

    def test_it_says_nothing_approves_or_merges(self) -> None:
        self.assertIn("nothing ever merges", self.text.lower())


class TheStaleClaimsAreFixedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = README.read_text()

    def test_the_test_count_is_current(self) -> None:
        """A table that understates the suite is the kind of stale detail that erodes trust.

        Compared against the discovered test count rather than a hardcoded number, because the first
        version of this test carried its own tolerance and went stale within one commit — exactly the
        failure it exists to catch.
        """
        import unittest

        table = re.search(r"\| Linux \| \*\*yes\*\* — (\d+) tests", self.text)
        self.assertIsNotNone(table)
        assert table is not None
        claimed = int(table.group(1))

        loader = unittest.TestLoader()
        discovered = loader.discover(str(Path(__file__).resolve().parent), top_level_dir=str(
            Path(__file__).resolve().parent.parent))

        def count(suite: unittest.TestSuite) -> int:
            return sum(count(item) if isinstance(item, unittest.TestSuite) else 1 for item in suite)

        actual = count(discovered)
        self.assertEqual(claimed, actual,
                         f"README says {claimed} tests; the suite discovers {actual}")

    def test_the_tui_is_not_still_described_as_awaiting_sign_off(self) -> None:
        self.assertNotIn("awaiting a redesign", self.text)


class EveryCommandInTheWalkthroughIsRealTests(unittest.TestCase):
    """The commands are extracted from the README and checked against the CLI's own parser.

    A walkthrough that drifts from the code is worse than none: it teaches a command that does not exist.
    """

    def test_every_documented_entry_point_parses(self) -> None:
        import code_shipper.cli as cli

        text = README.read_text()
        start = text.index("## First review")
        end = text.index("### Windows")
        commands = re.findall(r"python3 -m code_shipper\.cli ([a-z-]+)", text[start:end])
        self.assertTrue(commands, "no commands found in the walkthrough")
        parser = cli.build_parser()
        known: set[str] = set()
        for action in parser._actions:
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict):
                known = set(choices)
        for command in commands:
            self.assertIn(command, known, f"README documents `{command}`, which does not exist")

    def test_every_flag_the_walkthrough_names_exists(self) -> None:
        """Written after I put `--no-cache` in the README before the flag existed.

        A walkthrough that documents a flag the parser rejects is worse than no walkthrough: it fails for the
        reader, not for me.
        """
        import code_shipper.cli as cli

        text = README.read_text()
        start = text.index("## First review")
        end = text.index("### Windows")
        # Only flags on lines that invoke OUR cli. `hermes plugins install --enable` is Hermes's flag and
        # checking it against our parser would be a false failure.
        documented: set[str] = set()
        for line in text[start:end].splitlines():
            # Only lines that invoke our module. `hermes plugins install … --enable` is Hermes's flag;
            # checking it against our parser would be a false failure, and a test that cries wolf gets
            # its exceptions widened until it catches nothing.
            if "code_shipper.cli" in line:
                documented.update(re.findall(r"(?<![\w-])--[a-z][a-z-]+", line))
        self.assertTrue(documented, "no flags found in the walkthrough")

        parser = cli.build_parser()
        known: set[str] = set()
        for action in parser._actions:
            known.update(action.option_strings)
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict):
                for sub in choices.values():
                    for act in sub._actions:
                        known.update(act.option_strings)
        unknown = sorted(documented - known)
        self.assertEqual(unknown, [], f"the walkthrough names flags that do not exist: {unknown}")

    def test_the_cached_rerun_behaviour_is_documented(self) -> None:
        """Found by running the walkthrough: the second run exits 0 and that reads as a contradiction."""
        text = README.read_text()
        self.assertIn("already reviewed", text)
        self.assertIn("exits", text)
        self.assertIn("**0**", text)
        self.assertIn("--no-cache", text)

    def test_the_plugin_manifest_validates(self) -> None:
        """The walkthrough tells a reader to install this; it must be installable."""
        manifest = Path(__file__).resolve().parent.parent / "plugin" / "code-shipper" / "plugin.yaml"
        self.assertTrue(manifest.exists())
        body = manifest.read_text()
        for field in ("name:", "version:", "description:"):
            self.assertIn(field, body)


if __name__ == "__main__":
    unittest.main()
