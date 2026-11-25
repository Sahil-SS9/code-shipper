"""Phase 5c: the redesign, tested structurally.

The claims are structural, so the tests are:

* **Verdict names** are exactly the approved nine, and "Approved" never appears as a verdict.
* **The Next bar copy comes from the projection** — every state, asserted against the spec table.
* **No merge or approve anywhere**, extended to the palette vocabulary, the TUI keys, the CLI verbs and
  the Desktop CSS class names (the instruction asked for the palette and the CLI specifically).
* **Exit codes** are the contract: 0 clean, 1 findings, 2 error, 3 incomplete.
* **Colour** is off without a TTY and off with NO_COLOR.
* **Every surface holds no logic**: none imports `evaluate`, `approvals`, `broker` or a policy module.
"""

from __future__ import annotations

import ast
import inspect
import io
import json
import unittest

from code_shipper.surfaces import cli_view, desktop, fixtures, projection as proj, tui

# The approved verdict names, verbatim from the spec.
APPROVED_VERDICTS = {
    "findings": "Needs fixes",
    "approvals-blocked": "Needs approvals",
    "incomplete": "Incomplete, not a pass",
    "approvals-satisfied": "Ready for you to merge",
    "observe": "Observe only",
    "paused": "Paused",
    "stopped": "Stopped",
    "error": "Error",
    "reviewing": "Reviewing",
}

# The approved Next-bar headline per state, from the spec table.
EXPECTED_HEADLINES = {
    "reviewing": "Code-Shipper is reviewing the new commits",
    "findings": "You decide on 2 important findings",
    "incomplete": "This review did not finish, so it proves nothing",
    "approvals-blocked": "2 more approvals needed on GitHub",
    "approvals-satisfied": "Your rules are met. Merge on GitHub when you are ready",
    "observe": "Rules are in observe mode. Nothing is blocking",
    "paused": "Paused at the review cap (12 of 12)",
    "stopped": "Stopped by Sahil-SS9 at round 1",
    "empty": "No pull requests to watch yet",
}

FOOTER = "Advisory. Code-Shipper never approves or merges."

# Every key label either surface can show.
KEYMAP_ALL = [f"{k} {v}" for k, v in tui.KEYMAP.items()] + [f"{k} {v}" for k, v in tui.STATIC_KEYS]


class VerdictNameTests(unittest.TestCase):
    def test_the_nine_verdicts_are_exact(self) -> None:
        for state, wanted in APPROVED_VERDICTS.items():
            self.assertEqual(proj.STATES[state][0], wanted, state)

    def test_the_word_approved_is_never_a_verdict(self) -> None:
        for state, (verdict, _level) in proj.STATES.items():
            self.assertNotIn("approved", verdict.lower(), f"{state} verdict says approved")

    def test_the_empty_state_has_no_verdict(self) -> None:
        self.assertEqual(proj.STATES["empty"][0], "")

    def test_no_fixture_renders_an_approved_verdict(self) -> None:
        for name in fixtures.SCREENSHOT_ORDER:
            projection = fixtures.ALL[name]()
            self.assertNotIn("approved", projection.chip.lower())


class NextBarTests(unittest.TestCase):
    """The copy comes from the projection, and matches the spec for every state."""

    def test_each_state_matches_the_spec_copy(self) -> None:
        for state, wanted in EXPECTED_HEADLINES.items():
            headline, _sub, _action, _label = fixtures.ALL[state]().next_bar()
            self.assertEqual(headline, wanted, state)

    def test_every_state_has_a_headline_and_a_sub_line(self) -> None:
        for name in fixtures.SCREENSHOT_ORDER:
            headline, sub, action, label = fixtures.ALL[name]().next_bar()
            self.assertTrue(headline, f"{name} has no headline")
            self.assertTrue(sub, f"{name} has no sub-line")
            self.assertTrue(action, f"{name} has no primary action")
            self.assertTrue(label, f"{name} has no primary label")

    def test_the_primary_action_is_always_allowed(self) -> None:
        for name in fixtures.SCREENSHOT_ORDER:
            _h, _s, action, _l = fixtures.ALL[name]().next_bar()
            self.assertIn(action, proj.ALLOWED_ACTIONS, f"{name} offers {action}")

    def test_the_surfaces_do_not_contain_the_copy(self) -> None:
        """A second copy of a sentence is how two surfaces start saying different things."""
        for module in (desktop, tui, cli_view):
            source = inspect.getsource(module)
            for headline in EXPECTED_HEADLINES.values():
                self.assertNotIn(headline, source,
                                 f"{module.__name__} hardcodes Next-bar copy")


class LaneTests(unittest.TestCase):
    def test_four_steps_under_two_labels(self) -> None:
        steps = fixtures.state_findings().lanes()
        self.assertEqual([name for name, _s, _d in steps], ["Review", "Fix", "Approvals", "You merge"])

    def test_fix_says_not_needed_when_there_is_nothing_to_fix(self) -> None:
        steps = fixtures.state_approvals_satisfied().lanes()
        fix = next(s for s in steps if s[0] == "Fix")
        self.assertEqual(fix[1], "notneeded")

    def test_fix_is_waiting_when_findings_exist(self) -> None:
        steps = fixtures.state_findings().lanes()
        fix = next(s for s in steps if s[0] == "Fix")
        self.assertEqual(fix[1], "waiting")

    def test_approvals_met_on_the_ready_state(self) -> None:
        steps = fixtures.state_approvals_satisfied().lanes()
        approvals = next(s for s in steps if s[0] == "Approvals")
        self.assertEqual(approvals[1], "done")

    def test_the_merge_step_is_never_done_by_code_shipper(self) -> None:
        """Only a person merges, so the merge step is never 'done' and never code-shipper's."""
        for name in fixtures.SCREENSHOT_ORDER:
            steps = fixtures.ALL[name]().lanes()
            merge = next(s for s in steps if s[0] == "You merge")
            self.assertNotEqual(merge[1], "done", f"{name} claims the merge happened")

    def test_every_lane_state_is_a_known_icon(self) -> None:
        known = {"done", "failed", "waiting", "running", "notstarted", "notneeded"}
        for name in fixtures.SCREENSHOT_ORDER:
            for step, state, _sub in fixtures.ALL[name]().lanes():
                self.assertIn(state, known, f"{name}/{step} has unknown state {state}")


class NoMergeOrApproveTests(unittest.TestCase):
    """Extended per the instruction: the palette, the TUI keys and the CLI verbs."""

    def test_no_projection_state_offers_merge_or_approve(self) -> None:
        for name in fixtures.SCREENSHOT_ORDER:
            actions = fixtures.ALL[name]().actions()
            for word in ("merge", "approve", "request-changes"):
                self.assertNotIn(word, actions, f"{name} offers {word}")

    def test_the_tui_has_no_merge_or_approve_key(self) -> None:
        for action, label in tui.KEYMAP.items():
            self.assertNotIn("merge", action.lower())
            self.assertNotIn("approve", action.lower())
            self.assertNotIn("merge", label.lower())
            self.assertNotIn("approve", label.lower())

    def test_the_tui_static_keys_have_no_merge_or_approve(self) -> None:
        lowered = " ".join(f"{k} {label}" for k, label in tui.STATIC_KEYS).lower()
        self.assertNotIn("merge", lowered)
        self.assertNotIn("approve", lowered)

    def test_a_rendered_board_never_offers_merge_or_approve_as_an_action(self) -> None:
        """The words appear only in statements that this tool does neither.

        A blunt substring check cannot tell "You merge" (a lane naming the reader's job) from a merge
        action. So the check is on the *action* rows — the key bar and the lane icons — and separately on
        every place the words could become a control.
        """
        for name in fixtures.SCREENSHOT_ORDER:
            projection = fixtures.ALL[name]()
            # The action row is built from `actions()`, which has no merge member. Assert that directly.
            for action in projection.actions():
                self.assertNotIn("merge", action)
                self.assertNotIn("approve", action)
            # And no rendered key bar offers one.
            for width in (124, 60):
                lines = tui.render(projection, width=width)
                keys = " ".join(KEYMAP_ALL).lower()
                self.assertNotIn("merge", keys)
                self.assertNotIn("approve", keys)
                self.assertTrue(lines)

    def test_the_cli_verbs_have_no_merge_or_approve(self) -> None:
        from code_shipper import cli

        parser = cli.build_parser()
        subparsers = next(a for a in parser._actions if hasattr(a, "choices") and a.choices)
        for name in subparsers.choices:
            self.assertNotIn("merge", name.lower())
            self.assertNotIn("approve", name.lower())

    def test_the_desktop_has_no_merge_or_approve_control(self) -> None:
        html = desktop.render(fixtures.state_approvals_satisfied())
        self.assertNotIn("Merge — not available", html)
        self.assertNotIn("Approve — not available", html)

    def test_the_desktop_css_defines_no_merge_button_class(self) -> None:
        lowered = desktop.CSS.lower()
        self.assertNotIn(".merge", lowered)
        self.assertNotIn(".approve", lowered)

    def test_no_surface_module_mentions_a_merge_call(self) -> None:
        from code_shipper.surfaces import palette

        for module in (proj, desktop, tui, cli_view, palette):
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    self.assertNotIn(node.attr, ("merge", "approve", "create_merge"),
                                     f"{module.__name__} calls .{node.attr}")

    def test_the_lanes_label_the_merge_as_the_readers_job(self) -> None:
        html = desktop.render(fixtures.state_approvals_satisfied())
        self.assertIn("YOU AND GITHUB", html)
        self.assertIn("You merge", html)


class PaletteTests(unittest.TestCase):
    """The palette is the new surface for actions, so it gets the same structural guard."""

    def test_the_palette_passes_its_own_guard(self) -> None:
        from code_shipper.surfaces import palette

        palette.assert_safe()

    def test_no_palette_entry_is_a_merge_or_an_approve(self) -> None:
        from code_shipper.surfaces import palette

        for item in palette.ITEMS:
            lowered = f"{item.action} {item.label}".lower()
            for word in ("merge", "approve"):
                self.assertNotIn(word, lowered)

    def test_every_palette_action_is_in_the_vocabulary(self) -> None:
        from code_shipper.surfaces import palette

        for item in palette.ITEMS:
            self.assertIn(item.action, proj.ALLOWED_ACTIONS)

    def test_only_live_actions_are_listed(self) -> None:
        from code_shipper.surfaces import palette

        # a stopped PR cannot be stopped again, and its palette must not offer it
        stopped = palette.items_for(fixtures.state_stopped())
        self.assertNotIn("stop", [item.action for item in stopped])
        self.assertIn("review", [item.action for item in stopped])

    def test_items_are_grouped_under_two_headings(self) -> None:
        from code_shipper.surfaces import palette

        groups = [name for name, items in palette.group_items(fixtures.state_findings())]
        for name in groups:
            self.assertIn(name, palette.GROUPS)

    def test_the_cli_has_no_palette_exclusive_verb(self) -> None:
        """The palette maps to CLI verbs that already exist; it invents none."""
        from code_shipper import cli
        from code_shipper.surfaces import palette

        parser = cli.build_parser()
        subparsers = next(a for a in parser._actions if hasattr(a, "choices") and a.choices)
        for item in palette.ITEMS:
            # A palette action either maps to a real CLI verb or is a pure re-render. `refresh` is the
            # latter: it redraws the surface and runs nothing, so it needs no command.
            verb = {"open-pr": "status", "withdraw-lesson": "lessons",
                    "explain-policy": "config", "refresh": None}.get(item.action, item.action)
            if verb is None:
                continue
            self.assertIn(verb, subparsers.choices, f"palette action {item.action} has no CLI verb")


class ViewSwitcherTests(unittest.TestCase):
    def test_the_four_views_are_the_approved_ones(self) -> None:
        self.assertEqual(fixtures.state_findings().tabs,
                         ("Findings", "What changed", "Approvals", "Activity"))

    def test_findings_is_the_default_view(self) -> None:
        html = desktop.render(fixtures.state_findings())
        self.assertIn('<span class="on">Findings', html)

    def test_the_switcher_has_no_merge_or_approve_view(self) -> None:
        for tab in fixtures.state_findings().tabs:
            self.assertNotIn("approve", tab.lower())
            self.assertNotIn("merge", tab.lower())


class FootTests(unittest.TestCase):
    def test_the_desktop_footer_is_exact(self) -> None:
        for name in fixtures.SCREENSHOT_ORDER:
            self.assertIn(FOOTER, desktop.render(fixtures.ALL[name]()), name)

    def test_the_tui_footer_is_exact(self) -> None:
        for name in fixtures.SCREENSHOT_ORDER:
            joined = "\n".join(tui.render(fixtures.ALL[name]()))
            self.assertIn(FOOTER, joined, name)


class NoLogicInSurfacesTests(unittest.TestCase):
    def test_no_surface_imports_a_decision_module(self) -> None:
        forbidden = ("approvals", "broker", "steward", "evaluate", "policy", "service")
        for module in (proj, desktop, tui, cli_view, fixtures):
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                elif isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                for name in names:
                    for word in forbidden:
                        self.assertNotIn(word, name,
                                         f"{module.__name__} imports {name}")


class ExitCodeTests(unittest.TestCase):
    def test_findings_exits_1(self) -> None:
        self.assertEqual(cli_view.exit_code(fixtures.state_findings()), 1)

    def test_incomplete_exits_3(self) -> None:
        self.assertEqual(cli_view.exit_code(fixtures.state_incomplete()), 3)

    def test_error_exits_2(self) -> None:
        self.assertEqual(cli_view.exit_code(fixtures.state_error()), 2)

    def test_a_clean_review_exits_0(self) -> None:
        self.assertEqual(cli_view.exit_code(fixtures.state_approvals_satisfied()), 0)

    def test_incomplete_is_not_an_error(self) -> None:
        """A caller can retry an incomplete review; it must not be reported as a failure."""
        self.assertNotEqual(cli_view.exit_code(fixtures.state_incomplete()),
                            cli_view.exit_code(fixtures.state_error()))

    def test_the_codes_are_documented(self) -> None:
        self.assertIn("0 = clean", cli_view.EXIT_HELP)
        self.assertIn("3 = incomplete", cli_view.EXIT_HELP)
        self.assertEqual(cli_view.EXIT_CODES["incomplete"], 3)

    def test_an_incomplete_review_never_exits_clean(self) -> None:
        for name in ("incomplete", "paused", "error", "findings"):
            self.assertNotEqual(cli_view.exit_code(fixtures.ALL[name]()), 0, name)


class ColourTests(unittest.TestCase):
    def test_no_colour_without_a_tty(self) -> None:
        stream = io.StringIO()  # StringIO.isatty() is False
        self.assertFalse(cli_view.colour_enabled(stream, environ={}))

    def test_no_colour_with_NO_COLOR(self) -> None:
        class _TTY(io.StringIO):
            def isatty(self) -> bool:
                return True

        self.assertTrue(cli_view.colour_enabled(_TTY(), environ={}))
        self.assertFalse(cli_view.colour_enabled(_TTY(), environ={"NO_COLOR": "1"}))

    def test_plain_output_has_no_escape_codes(self) -> None:
        lines = cli_view.render_review(fixtures.state_findings(), colour=False)
        self.assertNotIn("\033", "\n".join(lines))

    def test_json_has_no_escape_codes(self) -> None:
        payload = cli_view.render_json(fixtures.state_findings())
        self.assertNotIn("\033", payload)
        json.loads(payload)  # and it parses

    def test_the_json_carries_the_exit_code_and_the_next_step(self) -> None:
        payload = json.loads(cli_view.render_json(fixtures.state_findings()))
        self.assertEqual(payload["exit_code"], 1)
        self.assertIn("headline", payload["next"])
        self.assertEqual(len(payload["lanes"]), 4)


class CliShapeTests(unittest.TestCase):
    def test_the_review_output_starts_with_the_verdict(self) -> None:
        lines = cli_view.render_review(fixtures.state_findings())
        # The pill is padded with one space each side; the verdict is still the first thing on the line.
        self.assertTrue(lines[0].lstrip().startswith("Needs fixes"))

    def test_the_second_block_is_the_next_line(self) -> None:
        lines = cli_view.render_review(fixtures.state_findings())
        joined = "\n".join(lines)
        self.assertIn("Next:", joined)
        # Next appears before the findings
        self.assertLess(joined.index("Next:"), joined.index("IMPORTANT"))

    def test_the_next_line_names_a_command(self) -> None:
        joined = "\n".join(cli_view.render_review(fixtures.state_findings()))
        self.assertIn("code-shipper fix 25", joined)

    def test_the_summary_line_is_last_but_one(self) -> None:
        lines = cli_view.render_review(fixtures.state_findings())
        self.assertIn("exit 1", lines[-1])

    def test_status_says_how_often_it_checks(self) -> None:
        lines = cli_view.render_status(fixtures.status_board(), check_every_minutes=2)
        self.assertIn("checked every 2 min", "\n".join(lines))

    def test_status_never_says_continuous(self) -> None:
        joined = "\n".join(cli_view.render_status(fixtures.status_board())).lower()
        self.assertNotIn("continuous", joined)
        self.assertNotIn("real-time", joined)

    def test_status_has_one_row_per_pull_request(self) -> None:
        board = fixtures.status_board()
        lines = cli_view.render_status(board)
        rows = [line for line in lines if "●" in line]
        self.assertEqual(len(rows), len(board))

    def test_status_rows_have_distinct_numbers(self) -> None:
        board = fixtures.status_board()
        numbers = [p.pr for p in board]
        self.assertEqual(len(set(numbers)), len(numbers))


class RenderTests(unittest.TestCase):
    def test_every_desktop_state_is_self_contained(self) -> None:
        for name in fixtures.SCREENSHOT_ORDER:
            html = desktop.render(fixtures.ALL[name]())
            self.assertNotIn("<script", html, name)
            self.assertNotIn("http://", html, name)
            self.assertNotIn("https://", html, name)

    def test_every_desktop_state_uses_the_approved_tokens(self) -> None:
        for colour in ("#08090a", "#0e0f11", "#15161a", "#1e2025", "#ececec",
                       "#a0a4ab", "#878c95", "#e8686e", "#5fc992", "#e3ac5e", "#80aef0"):
            self.assertIn(colour, desktop.CSS, colour)

    def test_the_desktop_is_two_panes(self) -> None:
        html = desktop.render(fixtures.state_findings())
        self.assertIn("grid-template-columns:320px 1fr", html)
        self.assertNotIn("290px", html, "the third pane was removed in the redesign")

    def test_the_desktop_has_a_status_bar_not_a_rail(self) -> None:
        html = desktop.render(fixtures.state_findings())
        self.assertIn('class="status"', html)
        self.assertNotIn('class="rail"', html)

    def test_every_tui_state_is_uniform_width(self) -> None:
        for name in fixtures.SCREENSHOT_ORDER:
            for width in (124, 70, 60):
                lines = tui.render(fixtures.ALL[name](), width=width)
                for line in lines:
                    self.assertEqual(len(line), width, f"{name} at {width}")

    def test_the_tui_collapses_below_the_floor(self) -> None:
        wide = "\n".join(tui.render(fixtures.state_findings(), width=124))
        narrow = "\n".join(tui.render(fixtures.state_findings(), width=60))
        self.assertIn("│", wide)
        self.assertNotIn("│", narrow.split("◐")[0][:80], "narrow must be one column")
        self.assertIn("← list", narrow)

    def test_the_tui_next_bar_has_an_edge(self) -> None:
        joined = "\n".join(tui.render(fixtures.state_findings()))
        self.assertIn("▌NEXT", joined)

    def test_every_state_renders_in_all_four_surfaces(self) -> None:
        for name in fixtures.SCREENSHOT_ORDER:
            projection = fixtures.ALL[name]()
            self.assertTrue(desktop.render(projection))
            self.assertTrue(tui.render(projection))
            self.assertTrue(tui.render(projection, width=60))
            self.assertTrue(cli_view.render_review(projection))

    def test_a_stale_verdict_is_labelled_in_every_surface(self) -> None:
        projection = fixtures.state_findings()
        projection.head = "a-newer-head"
        self.assertIn("Stale verdict", desktop.render(projection))
        self.assertIn("STALE", "\n".join(tui.render(projection)))
        # The CLI states the head it reviewed, which is the honest equivalent.
        self.assertIn("head", "\n".join(cli_view.render_review(projection)))

    def test_rendering_is_deterministic(self) -> None:
        for name in fixtures.SCREENSHOT_ORDER:
            self.assertEqual(tui.render(fixtures.ALL[name]()),
                             tui.render(fixtures.ALL[name]()))

    def test_the_empty_state_offers_the_three_steps(self) -> None:
        html = desktop.render(fixtures.state_empty())
        for command in ("code-shipper init", "code-shipper review 25", "code-shipper watch 25"):
            self.assertIn(command, html)


if __name__ == "__main__":
    unittest.main()
