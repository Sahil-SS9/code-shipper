"""Fix-loop scope tests: policy-driven, with one implementation of the rules.

The scope rules used to be hard-coded in fixloop as `calc/**` and `tests/**`, with a text-based
test-weakening check. They now live only in `code_shipper.steward.policy`, and this module calls them.
So these tests pass a policy explicitly, and the last class asserts fixloop has no scope logic of its
own left to drift.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from code_shipper.contain import fixloop
from code_shipper.contain.fixloop import check_scope, snapshot, test_weakening
from code_shipper.steward.policy import ScopePolicy

GOOD_SOURCE = """def add(a, b):
    return a + b


def divide(a, b):
    if b == 0:
        raise ZeroDivisionError("b must not be zero")
    return a / b
"""

BUGGY_SOURCE = GOOD_SOURCE.replace("a / b", "a * b")

GOOD_TESTS = """from calc import add, divide


def test_add():
    assert add(2, 3) == 5


def test_divide():
    assert divide(6, 3) == 2
"""

# What a repository would write in its own config for this layout.
SANDBOX_POLICY = ScopePolicy(
    allowed=("calc/**/*.py", "tests/**/*.py"),
    forbidden=(
        ".github/**",
        "**/setup.cfg",
        "**/pyproject.toml",
        "**/conftest.py",
        "**/.code-shipper/**",
    ),
)


class Workspace:
    """A throwaway repo copy: pristine original plus an editable workspace."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cs-scope-"))
        self.original = self.root / "original"
        self.work = self.root / "work"
        for base in (self.original, self.work):
            (base / "calc").mkdir(parents=True)
            (base / "tests").mkdir(parents=True)
            (base / "calc" / "__init__.py").write_text(BUGGY_SOURCE)
            (base / "tests" / "test_calc.py").write_text(GOOD_TESTS)

    def before(self) -> dict[str, str]:
        return snapshot(self.work)

    def check(self, before: dict[str, str], policy: ScopePolicy | None = None):
        return check_scope(
            before, self.work, original_root=self.original, policy=policy or SANDBOX_POLICY
        )

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class FixCase(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)


class AcceptsRealFixTests(FixCase):
    def test_a_source_only_fix_is_in_scope(self) -> None:
        before = self.ws.before()
        (self.ws.work / "calc" / "__init__.py").write_text(GOOD_SOURCE)

        verdict = self.ws.check(before)

        self.assertTrue(verdict.ok, verdict.problems)
        self.assertEqual(verdict.changed, ["calc/__init__.py"])

    def test_a_fix_that_also_touches_a_test_without_weakening_it_is_in_scope(self) -> None:
        before = self.ws.before()
        (self.ws.work / "calc" / "__init__.py").write_text(GOOD_SOURCE)
        (self.ws.work / "tests" / "test_calc.py").write_text(
            GOOD_TESTS + "\n\ndef test_divide_negative():\n    assert divide(-6, 3) == -2\n"
        )

        verdict = self.ws.check(before)

        self.assertTrue(verdict.ok, verdict.problems)
        self.assertIn("tests/test_calc.py", verdict.changed)

    def test_no_change_is_refused(self) -> None:
        verdict = self.ws.check(self.ws.before())
        self.assertFalse(verdict.ok)
        self.assertIn("no change was made", verdict.problems)

    def test_a_caller_with_no_policy_gets_a_refusal_not_a_pass(self) -> None:
        # the default policy allows no paths, so forgetting to pass one fails closed
        before = self.ws.before()
        (self.ws.work / "calc" / "__init__.py").write_text(GOOD_SOURCE)

        verdict = check_scope(before, self.ws.work, original_root=self.ws.original)

        self.assertFalse(verdict.ok)
        self.assertTrue(any("out of scope" in p for p in verdict.problems), verdict.problems)


class RefusesCheatingTests(FixCase):
    """Each of these makes CI green without fixing the bug."""

    def test_deleting_the_failing_test_is_refused(self) -> None:
        before = self.ws.before()
        (self.ws.work / "tests" / "test_calc.py").write_text(
            GOOD_TESTS.replace("\n\ndef test_divide():\n    assert divide(6, 3) == 2\n", "\n")
        )

        verdict = self.ws.check(before)

        self.assertFalse(verdict.ok)
        self.assertTrue(any("test removed" in p for p in verdict.problems), verdict.problems)

    def test_adding_a_skip_marker_is_refused(self) -> None:
        before = self.ws.before()
        (self.ws.work / "tests" / "test_calc.py").write_text(
            "import pytest\n\n"
            + GOOD_TESTS.replace("def test_divide():", "@pytest.mark.skip\ndef test_divide():")
        )

        verdict = self.ws.check(before)

        self.assertFalse(verdict.ok)
        self.assertTrue(any("skipped" in p for p in verdict.problems), verdict.problems)

    def test_removing_assertions_is_refused(self) -> None:
        before = self.ws.before()
        (self.ws.work / "tests" / "test_calc.py").write_text(
            GOOD_TESTS.replace("    assert divide(6, 3) == 2\n", "    pass\n")
        )

        verdict = self.ws.check(before)

        self.assertFalse(verdict.ok)
        self.assertTrue(any("assertions removed" in p for p in verdict.problems), verdict.problems)

    def test_touching_the_ci_workflow_is_refused(self) -> None:
        before = self.ws.before()
        workflow = self.ws.work / ".github" / "workflows"
        workflow.mkdir(parents=True)
        (workflow / "tests.yml").write_text("on: []\n")

        verdict = self.ws.check(before)

        self.assertFalse(verdict.ok)
        # Since N1 the CI workflow is refused as a protected path before scope is even consulted.
        self.assertTrue(any("protected path" in p and ".github/" in p for p in verdict.problems),
                        verdict.problems)

    def test_deleting_a_file_is_refused(self) -> None:
        before = self.ws.before()
        (self.ws.work / "calc" / "__init__.py").unlink()

        verdict = self.ws.check(before)

        self.assertFalse(verdict.ok)
        self.assertTrue(any("deleted" in p for p in verdict.problems), verdict.problems)

    def test_writing_outside_the_allowed_paths_is_refused(self) -> None:
        before = self.ws.before()
        (self.ws.work / "pytest.ini").write_text("[pytest]\n")

        verdict = self.ws.check(before)

        self.assertFalse(verdict.ok)
        self.assertTrue(any("out of scope" in p for p in verdict.problems), verdict.problems)

    def test_too_many_files_is_refused(self) -> None:
        before = self.ws.before()
        policy = ScopePolicy(allowed=("calc/**/*.py", "tests/**/*.py"), max_files=3)
        for index in range(5):
            (self.ws.work / "calc" / f"extra_{index}.py").write_text("x = 1\n")

        verdict = self.ws.check(before, policy=policy)

        self.assertFalse(verdict.ok)
        self.assertTrue(any("too many files" in p for p in verdict.problems), verdict.problems)

    def test_an_ast_only_cheat_is_caught(self) -> None:
        # a class method removed: invisible to the old text check, which matched '^def test_'
        two_tests = (
            "import unittest\n\n\nclass TestCalc(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n\n"
            "    def test_divide(self):\n        self.assertEqual(divide(6, 3), 2)\n"
        )
        one_test = (
            "import unittest\n\n\nclass TestCalc(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
        )
        # the pristine copy has both tests; the workspace keeps only one
        (self.ws.original / "tests" / "test_calc.py").write_text(two_tests)
        (self.ws.work / "tests" / "test_calc.py").write_text(two_tests)
        before = self.ws.before()
        (self.ws.work / "tests" / "test_calc.py").write_text(one_test)

        verdict = self.ws.check(before)

        self.assertFalse(verdict.ok)
        self.assertTrue(any("test removed" in p for p in verdict.problems), verdict.problems)


class TestWeakeningTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cs-weak-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        for base in ("original", "work"):
            (self.root / base / "tests").mkdir(parents=True)
            (self.root / base / "tests" / "test_x.py").write_text(GOOD_TESTS)

    def test_identical_trees_report_nothing(self) -> None:
        self.assertEqual(
            test_weakening(self.root / "original", self.root / "work", policy=SANDBOX_POLICY), []
        )

    def test_a_removed_test_is_reported(self) -> None:
        (self.root / "work" / "tests" / "test_x.py").write_text(
            GOOD_TESTS.replace("\n\ndef test_divide():\n    assert divide(6, 3) == 2\n", "\n")
        )
        problems = test_weakening(self.root / "original", self.root / "work", policy=SANDBOX_POLICY)
        self.assertTrue(any("test removed" in p for p in problems), problems)


class SnapshotTests(FixCase):
    def test_snapshot_ignores_git_internals(self) -> None:
        (self.ws.work / ".git").mkdir()
        (self.ws.work / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        self.assertNotIn(".git/HEAD", snapshot(self.ws.work))

    def test_snapshot_keys_are_posix(self) -> None:
        digests = snapshot(self.ws.work)
        self.assertIn("calc/__init__.py", digests)
        self.assertFalse(any("\\" in key for key in digests))

    def test_snapshot_detects_a_byte_change(self) -> None:
        before = snapshot(self.ws.work)
        path = self.ws.work / "calc" / "__init__.py"
        path.write_text(path.read_text() + "\n# trailing\n")
        self.assertNotEqual(before["calc/__init__.py"], snapshot(self.ws.work)["calc/__init__.py"])


class PathSeparatorTests(unittest.TestCase):
    """Rules must work on native separators. This was a real hole, not a cosmetic issue.

    On Windows a relative path comes back with backslashes. Before normalisation, a genuine fix looked
    out of scope and, worse, `calc\\\\conftest.py` matched no forbidden pattern either, so a file that
    can rewrite test collection would have been accepted.
    """

    def test_backslash_paths_are_judged_by_the_same_rules(self) -> None:
        self.assertTrue(SANDBOX_POLICY.path_allowed("calc\\__init__.py"))
        self.assertTrue(SANDBOX_POLICY.path_allowed("tests\\test_calc.py"))

    def test_forbidden_paths_are_caught_with_backslashes(self) -> None:
        for path in (
            "calc\\conftest.py",
            "conftest.py",
            ".github\\workflows\\tests.yml",
            "calc\\.code-shipper\\approvals.yml",
        ):
            with self.subTest(path=path):
                self.assertFalse(
                    SANDBOX_POLICY.path_allowed(path), f"{path} must be refused on any platform"
                )

    def test_scope_check_works_end_to_end_on_this_platform(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="cs-scope-sep-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        (root / "calc").mkdir()
        source = root / "calc" / "__init__.py"
        source.write_text("return a * b\n")
        before = fixloop.snapshot(root)
        source.write_text("return a / b\n")
        policy = ScopePolicy(allowed=("calc/**/*.py",))
        verdict = fixloop.check_scope(before, root, policy=policy)
        self.assertTrue(verdict.ok, verdict.problems)
        self.assertIn("calc/__init__.py", verdict.changed)


class SingleImplementationTests(unittest.TestCase):
    """The rules live in one place. This is the structural test the review asked for."""

    def test_fixloop_has_no_scope_patterns_left(self) -> None:
        for name in (
            "ALLOWED_PATTERNS",
            "FORBIDDEN_PATTERNS",
            "_tests_only_tightened",
            "_allowed",
            "_posix",
        ):
            with self.subTest(name=name):
                self.assertFalse(
                    hasattr(fixloop, name),
                    f"fixloop.{name} still exists: scope logic must live only in steward/policy.py",
                )

    def test_fixloop_source_carries_no_path_rules_of_its_own(self) -> None:
        import inspect

        source = inspect.getsource(fixloop)
        for needle in ("calc/", "ALLOWED_PATTERNS", "FORBIDDEN_PATTERNS", "conftest.py",
                       "@pytest", "assert "):
            self.assertNotIn(
                needle, source,
                f"fixloop mentions {needle!r}: path and test-weakening rules belong in policy.py",
            )

    def test_the_policy_module_owns_both_rules(self) -> None:
        import inspect

        from code_shipper.steward import policy

        source = inspect.getsource(policy)
        self.assertIn("def path_allowed", source)
        self.assertIn("def weakening", source)
        self.assertIn("import ast", source)


if __name__ == "__main__":
    unittest.main()
