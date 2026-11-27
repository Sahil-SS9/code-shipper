"""D16a: one sentence about merging, on every surface, in the same words.

Three surfaces had three phrasings — "It never approves and never merges" (Desktop), "You merge on GitHub"
(projection), "Merge on GitHub when you are ready" (next text) — so a user reading two of them could
reasonably think they said different things. This is the product's central safety promise; it reads
identically wherever it appears.
"""

from __future__ import annotations

import re
import unittest

from code_shipper.surfaces.mantra import NEVER_MERGES

EXPECTED = "Code-Shipper never merges. Merge on GitHub."


class TheSentenceIsExactlyAsSpecifiedTests(unittest.TestCase):
    def test_the_constant_is_the_agreed_sentence(self) -> None:
        self.assertEqual(NEVER_MERGES, EXPECTED)

    def test_it_names_the_actor_and_the_alternative(self) -> None:
        """Both halves matter: never merges, and where merging does happen."""
        self.assertIn("never merges", NEVER_MERGES)
        self.assertIn("Merge on GitHub", NEVER_MERGES)


class EverySurfaceSaysItTests(unittest.TestCase):
    def test_the_cli_summary_prints_it(self) -> None:
        rows = _render_board()
        self.assertIn(EXPECTED, "\n".join(rows))

    def test_the_tui_footer_carries_it(self) -> None:
        from code_shipper.surfaces import fixtures, tui

        projection = fixtures.ALL["findings"]()
        rendered = "\n".join(tui.render(projection, width=113, height=30))
        self.assertIn(EXPECTED, rendered)

    def test_the_narrow_tui_also_carries_it(self) -> None:
        """The degraded view is the one that is easiest to forget."""
        from code_shipper.surfaces import fixtures, tui

        projection = fixtures.ALL["findings"]()
        rendered = "\n".join(tui.render(projection, width=60, height=30))
        self.assertIn(EXPECTED, rendered)

    def test_the_desktop_page_contains_it(self) -> None:
        from code_shipper.surfaces import desktop, fixtures

        page = desktop.render(fixtures.ALL["findings"]())
        self.assertIn("Code-Shipper never merges. Merge on GitHub.", page)

    def test_the_desktop_page_escapes_it_rather_than_injecting_html(self) -> None:
        """It is escaped like every other constant that reaches the page."""
        source = (__file__.rsplit("/tests/", 1)[0] + "/code_shipper/surfaces/desktop.py")
        with open(source) as handle:
            text = handle.read()
        self.assertIn("{_esc(NEVER_MERGES)}", text)


class NoSurfaceKeepsItsOwnPhrasingTests(unittest.TestCase):
    def test_the_old_phrasings_are_gone(self) -> None:
        """Leaving one behind makes the sentence conditional on which screen you look at."""
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent / "code_shipper"
        offenders: list[str] = []
        for path in root.rglob("*.py"):
            if path.name == "mantra.py":
                continue  # the docstring names the old wordings on purpose; that is the record
            text = path.read_text()
            # Only real rendered strings. Comments mentioning the old wording are history and belong.
            for phrase in ('"It never approves and never merges"',
                           'Advisory. Code-Shipper never approves or merges.',
                           'Merge on GitHub when you are ready'):
                if phrase in text:
                    offenders.append(f"{path.name}: {phrase!r}")
        self.assertEqual(offenders, [], f"old phrasings remain: {offenders}")

    def test_it_is_defined_in_one_place(self) -> None:
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent / "code_shipper"
        definitions = [
            path.name for path in root.rglob("*.py")
            if re.search(r'^NEVER_MERGES\s*=', path.read_text(), re.MULTILINE)
        ]
        self.assertEqual(definitions, ["mantra.py"])


def _render_board() -> list[str]:
    """A real TUI render, used wherever a surface's output is needed."""
    from code_shipper.surfaces import fixtures, tui

    return tui.render(fixtures.ALL["findings"](), width=113, height=30)


if __name__ == "__main__":
    unittest.main()
