"""Every lens must carry the shared scope rule.

Both false confirms in the 20-seed measurement were scope creep, not misreading: `clamp` was reported for
not handling NaN (the contract never promised it) and `is_palindrome` for not stripping punctuation (the
docstring offered only case). A lens that checks what the code *says* it does cannot make that mistake, so
the rule is composed into every instruction rather than trusted to each author.

The enforcement is structural — `Lens.__post_init__` refuses an unscoped lens — and these tests assert the
structure rather than a list, so a seventh lens cannot slip through.
"""

from __future__ import annotations

import unittest

from code_shipper.review.lenses import (
    LENS_ORDER,
    LENSES,
    SCOPE_RULE,
    Lens,
    _scoped,
    select,
)


class EveryLensCarriesTheScopeRuleTests(unittest.TestCase):
    def test_every_lens_contains_the_rule(self) -> None:
        """Iterate the dict, not a hand-written list, so a new lens is covered automatically."""
        for name, lens in LENSES.items():
            with self.subTest(lens=name):
                self.assertIn(
                    SCOPE_RULE, lens.instruction,
                    f"lens {name!r} does not carry the shared scope rule",
                )

    def test_the_rule_is_one_constant_not_six_copies(self) -> None:
        """Six hand-edited copies drift. Assert the text appears exactly once, plus once per use."""
        from pathlib import Path

        source = (Path(__file__).resolve().parent.parent
                  / "code_shipper" / "review" / "lenses.py").read_text()
        # The literal lives in the constant; the instructions call _scoped(). The prose mentions
        # `_scoped()` in a docstring, so count call sites rather than the bare name.
        self.assertEqual(source.count("Report a problem only where the change contradicts"), 1)
        call_sites = source.count("            _scoped(")
        self.assertEqual(call_sites, 6, "every one of the six lenses must call _scoped()")

    def test_a_lens_that_omits_the_rule_cannot_be_constructed(self) -> None:
        """The proof that matters: it fails at construction, not at review time."""
        with self.assertRaises(ValueError) as caught:
            Lens(name="seventh", title="Seventh", instruction="Look for anything at all.")
        self.assertIn("scope rule", str(caught.exception))

    def test_a_new_lens_added_to_the_dict_is_covered(self) -> None:
        """A temporary entry — the test must not be able to pass just because it names today's lenses."""
        LENSES["_probe"] = Lens(
            name="_probe", title="Probe", instruction=_scoped("Look for a probe finding.")
        )
        try:
            self.assertIn(SCOPE_RULE, LENSES["_probe"].instruction)
            for name, lens in LENSES.items():
                self.assertIn(SCOPE_RULE, lens.instruction, name)
        finally:
            del LENSES["_probe"]

    def test_every_selected_lens_is_scoped(self) -> None:
        """The rule must reach the prompt, so check the objects the engine actually receives."""
        for level in ("quick", "standard", "deep"):
            for lens in select(level):
                with self.subTest(level=level, lens=lens.name):
                    self.assertIn(SCOPE_RULE, lens.instruction)


class TheRuleSaysWhatItNeedsToTests(unittest.TestCase):
    """Assert the rule's substance, so it cannot be weakened to pass the tests above."""

    def test_it_excludes_enhancements(self) -> None:
        self.assertIn("enhancement", SCOPE_RULE)
        self.assertIn("not a defect", SCOPE_RULE)

    def test_it_requires_a_named_violation(self) -> None:
        self.assertIn("cannot point to the line of the contract", SCOPE_RULE)

    def test_it_covers_the_docstring_case(self) -> None:
        """The is_palindrome false confirm was a docstring reading — name it explicitly."""
        self.assertIn("docstring", SCOPE_RULE)


class LensSelectionIsUnchangedTests(unittest.TestCase):
    def test_the_levels_still_select_the_same_lenses(self) -> None:
        self.assertEqual([lens.name for lens in select("quick")], ["combined"])
        self.assertEqual([lens.name for lens in select("standard")],
                         ["correctness", "security", "edge"])
        self.assertEqual([lens.name for lens in select("deep")], list(LENS_ORDER))

    def test_unknown_level_still_refused(self) -> None:
        with self.assertRaises(ValueError):
            select("thorough")


if __name__ == "__main__":
    unittest.main()
