"""`ui 25` works like `review 25`, and no two commands read as the same tool.

A5: `review 25` took the PR number positionally; `ui` restricted its positional to state names, so
`ui 25` failed with "invalid choice: '25'". Two commands that look identical behaving differently is the
kind of inconsistency that makes a user distrust every other command.

A6: `ui` and `ship-ui` had identical help text, so they read as two different tools; and the `lessons` help
said "inspect or withdraw" when `add` had existed for a round.
"""

from __future__ import annotations

import argparse
import unittest

from code_shipper import cli


def _parser_for(command: str) -> argparse.ArgumentParser:
    parser = cli.build_parser()
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict) and command in choices:
            return choices[command]
    raise AssertionError(f"no subparser for {command}")


class ANumberIsAPullRequestTests(unittest.TestCase):
    def test_ui_accepts_a_pr_number_positionally(self) -> None:
        """The exact failure: "invalid choice: '25'"."""
        args = _parser_for("ui").parse_args(["25"])
        self.assertEqual(args.state, "25")

    def test_ship_ui_accepts_a_pr_number_positionally(self) -> None:
        args = _parser_for("ship-ui").parse_args(["25"])
        self.assertEqual(args.state, "25")

    def test_review_and_ui_agree_on_the_shape(self) -> None:
        """The point of A5: the same gesture means the same thing on both commands."""
        self.assertEqual(_parser_for("review").parse_args(["25", "--repo", "o/n"]).pr, 25)
        self.assertEqual(_parser_for("ui").parse_args(["25"]).state, "25")

    def test_a_state_name_still_works(self) -> None:
        """The existing interface must not break: a known word is still a demo state."""
        for state in cli._DEMO_STATES:
            self.assertEqual(_parser_for("ui").parse_args([state]).state, state)

    def test_a_number_becomes_a_pr_in_the_projection(self) -> None:
        """Parsing is not enough — the handler has to read the number as a pull request."""
        args = argparse.Namespace(state="25", pr=None, repo="", demo=False, state_dir="")
        # The projection needs stored state, so assert the interpretation rather than the lookup: the
        # helper must move the number into `pr` and leave the state at "auto".
        from code_shipper.cli import _resolve_positional_pr

        _resolve_positional_pr(args)
        self.assertEqual(args.pr, 25)
        self.assertEqual(args.state, "auto")

    def test_an_explicit_pr_flag_wins_over_the_positional(self) -> None:
        args = argparse.Namespace(state="25", pr=7, repo="", demo=False, state_dir="")
        from code_shipper.cli import _resolve_positional_pr

        _resolve_positional_pr(args)
        self.assertEqual(args.pr, 7)

    def test_a_typo_is_refused_rather_than_drawing_the_recent_board(self) -> None:
        """Drawing something plausible for an unknown word is how a user ends up trusting a wrong screen."""
        args = argparse.Namespace(state="findngs", pr=None, repo="", demo=True, state_dir="")
        from code_shipper.cli import _projection_for

        with self.assertRaises(SystemExit) as caught:
            _projection_for(args)
        self.assertEqual(caught.exception.code, 2)


class TheHelpTextSaysWhatTheCommandsAreTests(unittest.TestCase):
    def test_ui_and_ship_ui_do_not_share_identical_help(self) -> None:
        ui_help = _parser_for("ui").description or _parser_for("ui").format_usage()
        ship_help = _parser_for("ship-ui").description or _parser_for("ship-ui").format_usage()
        self.assertNotEqual(ui_help, ship_help)
        # And the alias is stated, not implied.
        self.assertIn("alias", (ship_help or "").lower())

    def test_the_lessons_help_mentions_add(self) -> None:
        choices = _parser_for("lessons")._actions[1].choices
        self.assertIsNotNone(choices)
        assert choices is not None
        self.assertIn("add", choices)
        help_text = " ".join(
            action.help or "" for action in _parser_for("lessons")._actions
        )
        self.assertIn("add", help_text)

    def test_one_state_list_is_shared(self) -> None:
        """A state accepted by the parser and one the handler honours must not drift apart."""
        self.assertIn("findings", cli._DEMO_STATES)
        self.assertEqual(len(cli._DEMO_STATES), len(set(cli._DEMO_STATES)))


if __name__ == "__main__":
    unittest.main()
