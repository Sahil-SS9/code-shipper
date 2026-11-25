"""Phase 5b: the surfaces hold no logic, and no surface offers a merge.

Superseded in part by the approved Phase 5c redesign. The structural claims still hold and are tested
here; the assertions that described the rejected 5b *look* — text chips like `NEEDS-FIX`, a three-column
page, disabled Merge/Approve buttons, a 113-column board — were rewritten to the approved design. The
visual and copy assertions now live in `test_phase5c.py`; what remains here is the parts of 5b that were
right and must not regress: no merge action, one projection, an incomplete review is never a pass, stale
verdicts are labelled, and the TUI degrades rather than clipping.

The tests that matter here are structural, because the claims are structural:

* **Merge is absent from the action vocabulary**, not merely disabled — a test walks the whole module
  tree looking for a merge action.
* **Both surfaces render the same projection**, so parity is a property of construction rather than a
  thing to remember.
* **Colour is never the only channel**: every state has a text chip.
* **A stale verdict says so**, because a verdict pinned to a different head is the most dangerous thing
  a review surface can show without comment.
* **Below the width floor the board degrades to text**, rather than clipping its own actions.
"""

from __future__ import annotations

import ast
import inspect
import unittest

from code_shipper.surfaces import desktop, fixtures, projection as proj, tui


class NoMergeAnywhereTests(unittest.TestCase):
    """The hard rule: no merge action exists, in either surface or the projection."""

    def test_the_action_vocabulary_has_no_merge(self) -> None:
        for word in ("merge", "approve", "request-changes"):
            self.assertNotIn(word, proj.ALLOWED_ACTIONS)

    def test_no_state_offers_merge(self) -> None:
        for name in fixtures.SCREENSHOT_ORDER:
            actions = fixtures.ALL[name]().actions()
            for word in proj.FORBIDDEN_ACTIONS:
                self.assertNotIn(word, actions, f"state {name} offers {word}")

    def test_every_action_is_in_the_vocabulary(self) -> None:
        for name in fixtures.SCREENSHOT_ORDER:
            for action in fixtures.ALL[name]().actions():
                self.assertIn(action, proj.ALLOWED_ACTIONS,
                              f"{name} offered {action}, which is not an allowed action")

    def test_the_tui_module_has_no_merge_key(self) -> None:
        source = inspect.getsource(tui)
        for word in ('"merge"', "'merge'", "merge()"):
            self.assertNotIn(word, source)

    def test_the_desktop_has_no_merge_or_approve_control_at_all(self) -> None:
        """The redesign removed the disabled buttons; the lanes line and footer replace them.

        Superseded claim: 5b showed disabled controls as a teaching device. The approved design argued
        that a control you can see but not press invites hunting, and that naming whose job it is does
        the teaching better. Both designs refused the action; this one states the boundary in words.
        """
        html = desktop.render(fixtures.state_approvals_satisfied())
        for word in ("Merge — not available", "Approve — not available", "disabled"):
            self.assertNotIn(word, html)
        self.assertIn("YOU AND GITHUB", html)
        self.assertIn("You merge", html)

    def test_no_surface_module_imports_a_merge_call(self) -> None:
        for module in (proj, tui, desktop, fixtures):
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    self.assertNotIn(node.attr, ("merge", "approve", "create_merge"),
                                     f"{module.__name__} calls .{node.attr}")


class ProjectionTests(unittest.TestCase):
    def test_an_incomplete_review_is_not_a_verdict(self) -> None:
        projection = fixtures.state_incomplete()
        self.assertEqual(projection.state, "incomplete")
        self.assertIn("not a verdict", projection.refusal)

    def test_an_error_outranks_a_pause(self) -> None:
        projection = proj.build_from_review(
            repo="o/r", pr=1, title="t", head="a", reviewed_head="a", findings=[], complete=True,
            error="the policy is invalid", paused_reason="cap reached",
        )
        self.assertEqual(projection.state, "error")

    def test_a_pause_outranks_findings(self) -> None:
        projection = proj.build_from_review(
            repo="o/r", pr=1, title="t", head="a", reviewed_head="a",
            findings=[proj.Finding("a.py", 1, "important", "x")], complete=True,
            paused_reason="cap reached",
        )
        self.assertEqual(projection.state, "paused")

    def test_important_findings_outrank_approvals(self) -> None:
        projection = proj.build_from_review(
            repo="o/r", pr=1, title="t", head="a", reviewed_head="a",
            findings=[proj.Finding("a.py", 1, "important", "x")], complete=True,
            approvals_state="satisfied",
        )
        self.assertEqual(projection.state, "findings",
                         "an approved PR with open important findings is not ready")

    def test_nits_do_not_make_it_needs_fix(self) -> None:
        projection = proj.build_from_review(
            repo="o/r", pr=1, title="t", head="a", reviewed_head="a",
            findings=[proj.Finding("a.py", 1, "nit", "x")], complete=True,
            approvals_state="satisfied",
        )
        self.assertEqual(projection.verdict, "APPROVE-WITH-ISSUES")

    def test_the_four_signals_are_independent(self) -> None:
        projection = fixtures.state_findings()
        names = [signal.name for signal in projection.signals]
        self.assertEqual(names, ["review", "checks", "policy", "approval"])
        # The redesign draws these as four lanes rather than four cards; the rule is the same. What
        # follows is the substantive part: one green signal must not colour another.
        # a green CI row must not make the other signals look green
        checks = next(s for s in projection.signals if s.name == "checks")
        approval = next(s for s in projection.signals if s.name == "approval")
        self.assertTrue(checks.ok)
        self.assertFalse(approval.ok)

    def test_head_pinned_detects_a_stale_verdict(self) -> None:
        projection = fixtures.state_findings()
        self.assertTrue(projection.head_pinned)
        projection.head = "a-different-head"
        self.assertFalse(projection.head_pinned)

    def test_spend_is_unknown_not_zero(self) -> None:
        budget = proj.Budget(spend_usd=None)
        self.assertIn("unknown", budget.spend_text())
        self.assertNotIn("$0", budget.spend_text())

    def test_a_measured_spend_is_shown(self) -> None:
        self.assertIn("$1.20", proj.Budget(spend_usd=1.2).spend_text())

    def test_every_state_has_a_verdict_except_empty(self) -> None:
        for name in fixtures.SCREENSHOT_ORDER:
            projection = fixtures.ALL[name]()
            if name == "empty":
                self.assertEqual(projection.chip, "", "the empty state has no verdict")
            else:
                self.assertTrue(projection.chip, f"{name} has no verdict")
                self.assertNotEqual(projection.chip, "Unknown", f"{name} has no verdict")

    def test_the_authority_note_is_exact(self) -> None:
        # Superseded wording: 5b's note said the surfaces were unverified until the sandbox proof landed,
        # which stopped being true. The approved line is a standing rule, not a status.
        self.assertEqual(fixtures.state_findings().authority_note,
                         "Advisory. Code-Shipper never approves or merges.")


class TuiTests(unittest.TestCase):
    def test_every_state_renders_at_a_uniform_width(self) -> None:
        for name in fixtures.SCREENSHOT_ORDER:
            for width in (124, 70):
                lines = tui.render(fixtures.ALL[name](), width=width)
                for line in lines:
                    self.assertEqual(len(line), width, f"{name} produced an uneven line")

    def test_a_narrow_terminal_collapses_to_one_column(self) -> None:
        lines = tui.render(fixtures.state_findings(), width=60)
        joined = "\n".join(lines)
        # Superseded: 5b degraded to a plain-text summary. The redesign keeps the detail and folds the
        # list away, with an explicit way back.
        self.assertNotIn("│", "".join(lines[:4]), "no two-column board below the floor")
        self.assertIn("← list", joined)
        self.assertIn(fixtures.state_findings().chip, joined)

    def test_the_key_bar_shows_only_live_keys(self) -> None:
        # a stopped PR offers no stop
        lines = tui.render(fixtures.state_stopped(), width=124, height=34)
        footer = "\n".join(lines[-3:])
        self.assertIn("r review", footer)
        self.assertNotIn("s stop", footer)

    def test_a_running_review_can_be_stopped(self) -> None:
        lines = tui.render(fixtures.state_reviewing(), width=124, height=34)
        footer = "\n".join(lines[-3:])
        self.assertIn("s stop", footer)

    def test_a_stale_verdict_is_labelled(self) -> None:
        projection = fixtures.state_findings()
        projection.head = "a-newer-head"
        lines = tui.render(projection, width=124, height=34)
        self.assertIn("STALE", "\n".join(lines))

    def test_the_identity_line_is_present(self) -> None:
        first = tui.render(fixtures.state_findings())[0]
        self.assertIn("code-shipper", first)
        self.assertIn("code-shipper-sandbox", first)

    def test_rendering_is_deterministic(self) -> None:
        first = tui.render(fixtures.state_findings())
        second = tui.render(fixtures.state_findings())
        self.assertEqual(first, second)

    def test_an_empty_inbox_says_so(self) -> None:
        self.assertIn("nothing in the inbox", "\n".join(tui.render(fixtures.state_empty())))


class DesktopTests(unittest.TestCase):
    def test_every_state_produces_html(self) -> None:
        for name in fixtures.SCREENSHOT_ORDER:
            html = desktop.render(fixtures.ALL[name]())
            self.assertIn("<!doctype html>", html)

    def test_no_state_needs_javascript(self) -> None:
        for name in fixtures.SCREENSHOT_ORDER:
            html = desktop.render(fixtures.ALL[name]())
            self.assertNotIn("<script", html)

    def test_no_state_references_an_external_asset(self) -> None:
        for name in fixtures.SCREENSHOT_ORDER:
            html = desktop.render(fixtures.ALL[name]())
            self.assertNotIn("http://", html)
            self.assertNotIn("https://", html)
            self.assertNotIn("<img", html)
            self.assertNotIn("@import", html)

    def test_the_palette_matches_the_approved_redesign(self) -> None:
        # Superseded: the 5b palette was replaced by the approved 5c tokens.
        for colour in ("#08090a", "#0e0f11", "#ececec", "#e3ac5e", "#5fc992", "#e8686e", "#80aef0"):
            self.assertIn(colour, desktop.CSS)

    def test_the_four_lanes_render(self) -> None:
        html = desktop.render(fixtures.state_findings())
        self.assertEqual(html.count('class="step'), 4, "four lanes: Review, Fix, Approvals, You merge")

    def test_an_error_is_stated_plainly(self) -> None:
        """The redesign puts the plain cause in the Next bar instead of a separate blocker band."""
        html = desktop.render(fixtures.state_error())
        self.assertIn("error", html.lower())
        self.assertIn("count (2) exceeds", html)

    def test_a_stale_verdict_warns(self) -> None:
        projection = fixtures.state_findings()
        projection.head = "a-newer-head"
        self.assertIn("Stale verdict", desktop.render(projection))

    def test_the_status_bar_shows_the_budget_and_the_unknown_spend(self) -> None:
        html = desktop.render(fixtures.state_findings())
        self.assertIn("round", html)
        self.assertIn("pushes", html)
        self.assertIn("spend unknown", html)

    def test_html_is_escaped(self) -> None:
        projection = fixtures.state_findings()
        projection.title = "<script>alert(1)</script>"
        html = desktop.render(projection)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
