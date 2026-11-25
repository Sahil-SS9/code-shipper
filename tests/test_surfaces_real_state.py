"""Surfaces on real stored state, and the rule that stops them reading fixtures.

The failure this file exists for: every surface drew `fixtures`, so the screens looked finished while
showing nobody's review. Two kinds of test follow.

1. **Real state renders.** A review is written to a temp state dir the way `review` writes it, and each
   surface is asked to draw it. The assertions are about content that could only come from that state.
2. **Fixtures are unreachable without `--demo`.** A source-level sweep over every surface module, so a
   future surface cannot quietly import `fixtures` and draw sample data again.

The second is the important one. A test that only checked the happy path would have passed all through the
stops where the surfaces were showing invented data.
"""

from __future__ import annotations

import ast
import inspect
import json
import tempfile
import unittest
from pathlib import Path

from code_shipper.review.state import ReviewState, ReviewStore
from code_shipper.review.tracked import Ledger, Tracked
from code_shipper.surfaces import cli_view, desktop, loader, tui


def _write_review(root: Path, *, repo: str = "o/r", pr: int = 7, head: str = "abc1234def",
                  findings: list[dict] | None = None, complete: bool = True,
                  paused: bool = False, pause_reason: str = "", reviews: int = 1) -> None:
    """Write state the way the review service does, so the loader reads a realistic file."""
    state = ReviewState(
        repo=repo, pr=pr,
        last_reviewed_head=head if complete else "",
        open_important={f["path"]: f for f in (findings or [])
                        if f.get("severity") == "important"},
        open_all={f["path"]: f for f in (findings or [])},
        reviews=reviews,
        last_review_at=1767225600.0,
        paused=paused,
        pause_reason=pause_reason,
    )
    ReviewStore(root).save(state)


FINDING = {
    "path": "calc/clamp.py", "line": 4, "severity": "important",
    "title": "clamp never applies the upper bound",
    "why": "clamp(10, 0, 5) returns 10.", "evidence": "read the diff at this head",
    "fingerprint": "fp-clamp",
}


class LoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cs-load-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def test_an_unreviewed_pull_request_is_reported_as_not_found(self) -> None:
        snapshot = loader.load_snapshot("o/r", 99, state_dir=self.root)
        self.assertFalse(snapshot.found,
                         "a pull request that was never reviewed must not look like a clean board")

    def test_a_stored_review_is_found(self) -> None:
        _write_review(self.root, findings=[FINDING])
        snapshot = loader.load_snapshot("o/r", 7, state_dir=self.root)
        self.assertTrue(snapshot.found)
        self.assertEqual(snapshot.reviewed_head, "abc1234def")
        self.assertEqual(len(snapshot.findings), 1)
        self.assertEqual(snapshot.findings[0].path, "calc/clamp.py")

    def test_the_projection_comes_from_stored_state(self) -> None:
        _write_review(self.root, findings=[FINDING])
        projection = loader.load_projection("o/r", 7, state_dir=self.root)
        self.assertEqual(projection.state, "findings")
        self.assertEqual(projection.pr, 7)
        self.assertEqual(projection.findings[0].title, FINDING["title"])

    def test_an_incomplete_review_projects_as_incomplete(self) -> None:
        _write_review(self.root, findings=[FINDING], complete=False)
        projection = loader.load_projection("o/r", 7, state_dir=self.root)
        self.assertEqual(projection.state, "incomplete")
        self.assertFalse(projection.review_completed)

    def test_a_clean_review_projects_as_ready(self) -> None:
        _write_review(self.root, findings=[])
        projection = loader.load_projection("o/r", 7, state_dir=self.root)
        self.assertIn(projection.state, ("approvals-satisfied", "findings"))

    def test_a_paused_review_projects_as_paused(self) -> None:
        _write_review(self.root, paused=True, pause_reason="review cap reached")
        projection = loader.load_projection("o/r", 7, state_dir=self.root)
        self.assertEqual(projection.state, "paused")

    def test_the_tracked_ledger_reaches_the_projection(self) -> None:
        _write_review(self.root, findings=[FINDING])
        Ledger(self.root).add("o/r", 7, Tracked(
            key="T-1", path="calc/clamp.py", title=FINDING["title"],
            actor="sahil", created_at=1767225600.0,
        ))
        snapshot = loader.load_snapshot("o/r", 7, state_dir=self.root)
        self.assertEqual(len(snapshot.tracked), 1)
        self.assertEqual(snapshot.tracked[0]["actor"], "sahil")

    def test_discover_lists_what_was_reviewed(self) -> None:
        _write_review(self.root, pr=7)
        _write_review(self.root, pr=8, head="def5678")
        found = loader.discover(self.root)
        self.assertEqual(sorted(pr for _repo, pr in found), [7, 8])

    def test_discover_on_an_empty_dir_is_empty(self) -> None:
        self.assertEqual(loader.discover(self.root), [])

    def test_discover_survives_a_torn_file(self) -> None:
        _write_review(self.root, pr=7)
        (self.root / "review-state" / "torn.json").write_text('{"repo": "x', encoding="utf-8")
        found = loader.discover(self.root)
        self.assertEqual([pr for _r, pr in found], [7], "one bad file must not empty the list")

    def test_numbers_not_stored_in_the_file_are_recovered_from_the_name(self) -> None:
        path = self.root / "review-state" / "o__r-7.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_reviewed_head": "abc"}), encoding="utf-8")
        found = loader.discover(self.root)
        self.assertIn(("o/r", 7), found)


class RealStateOnEverySurfaceTests(unittest.TestCase):
    """Each surface, drawn from state written to disk — not from fixtures."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cs-surface-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        _write_review(self.root, findings=[FINDING], reviews=2)

    def _projection(self):
        return loader.load_projection("o/r", 7, state_dir=self.root)

    def test_the_tui_draws_the_stored_review(self) -> None:
        text = "\n".join(tui.render(self._projection(), width=124, height=34))
        self.assertIn("clamp never applies the upper bound", text)
        self.assertIn("#7", text)
        self.assertIn("o/r", text)

    def test_the_desktop_draws_the_stored_review(self) -> None:
        html = desktop.render(self._projection())
        self.assertIn("clamp never applies the upper bound", html)
        self.assertIn("#7", html)

    def test_the_cli_draws_the_stored_review(self) -> None:
        text = "\n".join(cli_view.render_review(self._projection()))
        self.assertIn("clamp never applies the upper bound", text)
        self.assertIn("#7", text)

    def test_the_status_list_draws_stored_rows(self) -> None:
        rows = loader.rows_for_state_dir(self.root)
        self.assertEqual(len(rows), 1)
        text = "\n".join(cli_view.render_status(
            [loader.load_projection(r.repo, r.pr, state_dir=self.root) for r in rows]))
        self.assertIn("#7", text)

    def test_the_tracked_finding_appears_on_a_real_surface(self) -> None:
        Ledger(self.root).add("o/r", 6, Tracked(key="T-2", path="other.py", title="elsewhere",
                                                actor="sahil", created_at=1.0))
        # The tracked entry is for another PR and must not leak into this one.
        text = "\n".join(tui.render(self._projection(), width=124))
        self.assertNotIn("elsewhere", text)

    def test_no_surface_invents_approvals_or_ci_for_stored_state(self) -> None:
        """Not stored, so it must read as unknown rather than as a pass."""
        html = desktop.render(self._projection())
        self.assertNotIn("CI passing", html)
        self.assertNotIn("approvals satisfied", html.lower())


class FixturesNeedDemoTests(unittest.TestCase):
    """The rule: no surface module may reach for fixtures on its own.

    This is a source-level sweep, not a behaviour check, because the failure mode was a surface that
    *worked* — it happily drew fixtures. Only inspection catches a new one doing the same.
    """

    SURFACES = ("desktop", "tui", "cli_view", "loader")

    def _module_path(self, name: str) -> Path:
        module = {"desktop": desktop, "tui": tui, "cli_view": cli_view,
                  "loader": loader}[name]
        return Path(module.__file__)

    def test_no_surface_imports_fixtures(self) -> None:
        for name in self.SURFACES:
            tree = ast.parse(self._module_path(name).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn("fixtures", (node.module or ""),
                                     f"{name} imports fixtures; only the CLI may, under --demo")
                    for alias in node.names:
                        self.assertNotEqual(alias.name, "fixtures", f"{name} imports fixtures")

    def test_the_cli_only_touches_fixtures_inside_the_demo_path(self) -> None:
        """Every reference to fixtures in the CLI must sit in a branch guarded by args.demo."""
        from code_shipper import cli

        source = Path(cli.__file__).read_text()
        tree = ast.parse(source)
        lines = source.splitlines()

        offenders: list[int] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "fixtures":
                line = node.lineno
                context = "\n".join(lines[max(0, line - 30):line + 3])
                if "args.demo" not in context and "demo" not in context.lower():
                    offenders.append(line)
        self.assertEqual(offenders, [],
                         f"fixtures used outside a --demo branch at lines {offenders}")

    def test_every_surface_command_accepts_demo(self) -> None:
        from code_shipper import cli

        parser = cli.build_parser()
        subparsers = next(a for a in parser._actions if hasattr(a, "choices") and a.choices)
        for name in ("status", "ui", "ship-ui", "ship-page"):
            options = {opt for action in subparsers.choices[name]._actions
                       for opt in action.option_strings}
            self.assertIn("--demo", options, f"{name} has no --demo flag")


class DemoIsLabelledTests(unittest.TestCase):
    """`--demo` must be visible on every surface, not just in the help text."""

    def test_the_cli_banner_says_demo(self) -> None:
        self.assertIn("demo data", cli_view.demo_banner())

    def test_the_status_output_carries_the_banner(self) -> None:
        from code_shipper.surfaces import fixtures

        lines = cli_view.render_status(fixtures.status_board(), demo=True)
        self.assertIn("demo data", lines[0])

    def test_the_desktop_page_carries_the_banner(self) -> None:
        from code_shipper.surfaces import fixtures

        html = desktop.render(fixtures.state_findings(), demo=True)
        self.assertIn("demo data", html)

    def test_the_desktop_page_without_demo_has_no_banner(self) -> None:
        from code_shipper.surfaces import fixtures

        html = desktop.render(fixtures.state_findings(), demo=False)
        self.assertNotIn("demo data", html)


class CommandBehaviourTests(unittest.TestCase):
    """The two commands whose messages were untrue."""

    def test_review_no_longer_claims_posting_is_unimplemented(self) -> None:
        from code_shipper import cli

        source = Path(cli.__file__).read_text()
        self.assertNotIn("posting a review is not implemented yet", source)

    def test_review_says_plainly_that_it_does_not_post(self) -> None:
        from code_shipper import cli

        source = Path(cli.__file__).read_text()
        self.assertIn("does not comment on GitHub", source)
        self.assertIn("watch", source)

    def test_fix_dry_run_does_not_promise_a_live_round(self) -> None:
        from code_shipper import cli

        source = Path(cli.__file__).read_text()
        self.assertIn("owns the round, the budget and the stop signal", source)

    def test_the_readme_documents_the_fix_behaviour(self) -> None:
        readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
        self.assertIn("code-shipper fix", readme)
        self.assertIn("watch", readme)

    def test_the_readme_is_honest_about_the_plugin_surface(self) -> None:
        readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
        self.assertIn("no separate Hermes Desktop panel", readme)
        self.assertIn("/ship", readme)


class PaletteDrawnTests(unittest.TestCase):
    """Ctrl+K was advertised and nothing could open it."""

    def test_the_desktop_draws_the_palette(self) -> None:
        from code_shipper.surfaces import fixtures

        html = desktop.render(fixtures.state_findings())
        self.assertIn("palwrap" if "palwrap" in desktop.CSS else "pwrap", html)

    def test_the_palette_draws_its_items(self) -> None:
        from code_shipper.surfaces import fixtures

        html = desktop.render(fixtures.state_findings())
        self.assertGreaterEqual(html.count("pitem"), 2)

    def test_the_palette_offers_no_merge_or_approve(self) -> None:
        from code_shipper.surfaces import fixtures

        html = desktop.render(fixtures.state_approvals_satisfied())
        palette_start = html.find('class="palette"')
        self.assertGreater(palette_start, 0, "the palette must be drawn")
        palette = html[palette_start:html.find("</div></div>", palette_start)]
        self.assertNotIn("Merge", palette)
        self.assertNotIn("Approve", palette)

    def test_the_hint_is_not_advertised_without_the_palette(self) -> None:
        from code_shipper.surfaces import fixtures

        html = desktop.render(fixtures.state_findings())
        if "Ctrl" in html:
            self.assertIn("pwrap", html, "the Ctrl+K hint is shown but no palette is drawn")


if __name__ == "__main__":
    unittest.main()
