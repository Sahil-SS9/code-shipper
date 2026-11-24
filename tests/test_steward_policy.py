"""P3-B: scope from policy, AST test-weakening, budgets.

Three carried-forward items are closed here:

1. **Scope comes from policy**, not from the hard-coded `calc/**` sandbox paths (M0-GATE known limit).
2. **Test weakening is decided from the AST**, so class methods, unittest-style asserts and
   `assert True` padding are all caught (M0-GATE known limit; the text check missed all three).
3. **Budgets** — rounds, pushes per day, wall clock, and a spend cap that is refused rather than
   approximated when the adapter cannot enforce it (T-UNIT-BUDGET, T-ADV-09/10/11).
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from code_shipper.budget import BudgetExceeded, RunBudget
from code_shipper.steward.policy import ScopePolicy, analyse_tests, weakening

GOOD_TESTS = '''import unittest

from calc import add, divide


class TestCalc(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)

    def test_divide(self):
        self.assertEqual(divide(6, 3), 2)

    def test_divide_by_zero(self):
        with self.assertRaises(ZeroDivisionError):
            divide(1, 0)


def test_module_level():
    assert add(1, 1) == 2
'''


class ScopePolicyTests(unittest.TestCase):
    """Scope comes from configuration. No default allowed paths: a caller must opt in."""

    def test_defaults_allow_nothing(self) -> None:
        # the safe default: a policy with no allowed patterns permits no writes at all
        policy = ScopePolicy()
        self.assertFalse(policy.path_allowed("calc/__init__.py"))
        self.assertFalse(policy.path_allowed("anything.py"))

    def test_allowed_patterns_are_honoured(self) -> None:
        policy = ScopePolicy(allowed=("calc/**", "tests/**"))
        self.assertTrue(policy.path_allowed("calc/__init__.py"))
        self.assertTrue(policy.path_allowed("tests/test_calc.py"))
        self.assertFalse(policy.path_allowed("README.md"))

    def test_forbidden_wins_over_allowed(self) -> None:
        policy = ScopePolicy(allowed=("calc/**",), forbidden=("**/conftest.py", "calc/secret.py"))
        self.assertFalse(policy.path_allowed("calc/conftest.py"))
        self.assertFalse(policy.path_allowed("calc/secret.py"))
        self.assertTrue(policy.path_allowed("calc/other.py"))

    def test_backslashes_are_normalised(self) -> None:
        # a Windows path must be judged by the same rules as a POSIX one
        policy = ScopePolicy(allowed=("calc/**",), forbidden=("**/conftest.py",))
        self.assertTrue(policy.path_allowed("calc\\__init__.py"))
        self.assertFalse(policy.path_allowed("calc\\conftest.py"))

    def test_glob_star_does_not_cross_directories(self) -> None:
        policy = ScopePolicy(allowed=("tests/*.py",))
        self.assertTrue(policy.path_allowed("tests/test_a.py"))
        self.assertFalse(policy.path_allowed("tests/deep/test_a.py"))

    def test_double_star_crosses_directories(self) -> None:
        policy = ScopePolicy(allowed=("tests/**",))
        self.assertTrue(policy.path_allowed("tests/deep/nested/test_a.py"))

    def test_test_paths_are_recognised(self) -> None:
        policy = ScopePolicy(allowed=("calc/**", "tests/**"))
        for path in ("tests/test_a.py", "calc/test_inner.py", "src/thing_test.py"):
            with self.subTest(path=path):
                self.assertTrue(policy.is_test_path(path))
        self.assertFalse(policy.is_test_path("calc/__init__.py"))

    def test_policy_is_built_from_config(self) -> None:
        policy = ScopePolicy.from_config(
            {"allowed": ["src/**"], "forbidden": ["**/conftest.py"], "max_files": 3}
        )
        self.assertTrue(policy.path_allowed("src/a.py"))
        self.assertEqual(policy.max_files, 3)

    def test_unknown_config_keys_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            ScopePolicy.from_config({"allowed": ["x/**"], "permit_all": True})

    def test_a_non_list_pattern_set_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            ScopePolicy.from_config({"allowed": "src/**"})


class AstWeakeningTests(unittest.TestCase):
    """The M0 carry-forward: the text check missed all three of these."""

    def test_a_removed_class_method_is_caught(self) -> None:
        # Phase 0 matched '^def test_' at line start, so an indented method was invisible
        after = GOOD_TESTS.replace(
            "    def test_divide(self):\n        self.assertEqual(divide(6, 3), 2)\n\n", ""
        )
        self.assertIn("test removed: test_divide", " ".join(weakening(GOOD_TESTS, after)))

    def test_a_removed_unittest_assertion_is_caught(self) -> None:
        # no bare `assert` keyword involved, so an assertion-count check had to count method calls
        after = GOOD_TESTS.replace(
            "        self.assertEqual(add(2, 3), 5)", "        pass"
        )
        problems = weakening(GOOD_TESTS, after)
        self.assertTrue(any("assertions removed from test_add" in p for p in problems), problems)

    def test_assert_true_padding_is_caught(self) -> None:
        after = GOOD_TESTS + "\n\ndef test_padding():\n    assert True\n"
        problems = weakening(GOOD_TESTS, after)
        self.assertTrue(any("constant assert added" in p for p in problems), problems)

    def test_adding_a_pytest_skip_is_caught(self) -> None:
        after = GOOD_TESTS.replace(
            "    def test_divide(self):", "    @pytest.mark.skip\n    def test_divide(self):"
        )
        problems = weakening(GOOD_TESTS, after)
        self.assertTrue(any("skipped: test_divide" in p for p in problems), problems)

    def test_adding_a_unittest_skip_is_caught(self) -> None:
        after = GOOD_TESTS.replace(
            "    def test_divide(self):",
            "    @unittest.skip(\"temporarily\")\n    def test_divide(self):",
        )
        problems = weakening(GOOD_TESTS, after)
        self.assertTrue(any("skipped: test_divide" in p for p in problems), problems)

    def test_deleting_the_whole_file_is_caught(self) -> None:
        problems = weakening(GOOD_TESTS, "")
        self.assertTrue(any("test removed" in p for p in problems), problems)

    def test_a_legitimate_addition_is_allowed(self) -> None:
        after = GOOD_TESTS + "\n\ndef test_new():\n    assert add(2, 2) == 4\n"
        self.assertEqual(weakening(GOOD_TESTS, after), [])

    def test_rewriting_an_assertion_without_losing_one_is_allowed(self) -> None:
        after = GOOD_TESTS.replace("self.assertEqual(add(2, 3), 5)", "self.assertEqual(add(3, 2), 5)")
        self.assertEqual(weakening(GOOD_TESTS, after), [])

    def test_a_skipped_test_that_was_already_skipped_is_not_a_regression(self) -> None:
        already = GOOD_TESTS.replace(
            "    def test_divide(self):", "    @pytest.mark.skip\n    def test_divide(self):"
        )
        self.assertEqual(weakening(already, already), [])

    def test_unparseable_source_does_not_raise(self) -> None:
        # a file that does not parse is a fact, not a crash
        facts = analyse_tests("def broken(:\n")
        self.assertEqual(facts.tests, {})

    def test_the_analysis_counts_assertions_per_test(self) -> None:
        facts = analyse_tests(GOOD_TESTS)
        self.assertEqual(facts.tests["test_add"], 1)
        self.assertEqual(facts.tests["test_module_level"], 1)


class BudgetTests(unittest.TestCase):
    """T-UNIT-BUDGET, T-ADV-09/10/11."""

    def test_a_fresh_budget_allows_work(self) -> None:
        b = RunBudget(fix_rounds=3, pushes_per_day=10, wall_clock_seconds=60)
        b.start_round()
        b.record_push()
        self.assertEqual(b.rounds_used, 1)
        self.assertEqual(b.pushes_used, 1)

    def test_rounds_are_capped(self) -> None:
        b = RunBudget(fix_rounds=2)
        b.start_round()
        b.start_round()
        with self.assertRaises(BudgetExceeded) as caught:
            b.start_round()
        self.assertEqual(caught.exception.limit, "fix_rounds")

    def test_pushes_are_capped(self) -> None:
        b = RunBudget(pushes_per_day=2)
        b.record_push()
        b.record_push()
        with self.assertRaises(BudgetExceeded) as caught:
            b.record_push()
        self.assertEqual(caught.exception.limit, "pushes_per_day")

    def test_the_wall_clock_is_capped(self) -> None:
        now = [1000.0]
        b = RunBudget(wall_clock_seconds=30, clock=lambda: now[0])
        now[0] += 31
        with self.assertRaises(BudgetExceeded) as caught:
            b.check_clock()
        self.assertEqual(caught.exception.limit, "wall_clock")

    def test_a_spend_cap_that_cannot_be_enforced_is_refused(self) -> None:
        # better to refuse than to run believing a cap is in force
        with self.assertRaises(ValueError):
            RunBudget(daily_usd=5.0, enforceable=False)

    def test_unknown_spend_is_recorded_as_unknown_not_zero(self) -> None:
        b = RunBudget(daily_usd=10.0)
        b.record_cost(None)
        self.assertEqual(b.spend_used, 0.0)
        self.assertEqual(b.unknown_cost_runs, 1)

    def test_the_spend_cap_is_enforced_when_known(self) -> None:
        b = RunBudget(daily_usd=1.0)
        b.record_cost(0.6)
        with self.assertRaises(BudgetExceeded) as caught:
            b.record_cost(0.6)
        self.assertEqual(caught.exception.limit, "daily_usd")

    def test_a_budget_with_no_cap_accepts_any_cost(self) -> None:
        b = RunBudget()
        b.record_cost(999.0)
        self.assertEqual(b.spend_used, 999.0)

    def test_remaining_rounds_and_pushes_are_reported(self) -> None:
        b = RunBudget(fix_rounds=3, pushes_per_day=10)
        b.start_round()
        b.record_push()
        self.assertEqual(b.rounds_remaining, 2)
        self.assertEqual(b.pushes_remaining, 9)

    def test_non_positive_limits_are_refused(self) -> None:
        for kwargs in ({"fix_rounds": 0}, {"pushes_per_day": 0}, {"wall_clock_seconds": 0}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    RunBudget(**kwargs)

    def test_a_budget_cannot_be_reset_by_a_second_start(self) -> None:
        # T-ADV-10: a loop cannot extend its own allowance by calling start_round again
        b = RunBudget(fix_rounds=1)
        b.start_round()
        with self.assertRaises(BudgetExceeded):
            b.start_round()


class ScopeIntegrationTests(unittest.TestCase):
    """Policy-driven scope against a real tree, replacing the hard-coded sandbox paths."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cs-scope2-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("x = 1\n")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_app.py").write_text(GOOD_TESTS)

    def test_a_policy_over_a_different_layout_works(self) -> None:
        # this repository has src/ and tests/, not calc/. Nothing about the rules is specific to the
        # sandbox any more.
        policy = ScopePolicy(allowed=("src/**", "tests/**"), forbidden=("**/conftest.py",))
        self.assertTrue(policy.path_allowed("src/app.py"))
        self.assertTrue(policy.path_allowed("tests/test_app.py"))
        self.assertFalse(policy.path_allowed("package.json"))

    def test_a_test_file_change_is_checked_by_the_ast(self) -> None:
        before = (self.root / "tests" / "test_app.py").read_text()
        after = before.replace(
            "    def test_divide(self):\n        self.assertEqual(divide(6, 3), 2)\n\n", ""
        )
        self.assertTrue(weakening(before, after))


if __name__ == "__main__":
    unittest.main()
