"""Tests for the host-side fix scope rules.

The worker runs untrusted code with file-edit tools. Containment stops it reaching credentials or the
network; it does not stop it rewriting a test so CI goes green without fixing anything. These tests
cover that case and the ordinary ones, in both directions: real fixes accepted, cheats refused.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from code_shipper.contain.fixloop import check_scope, snapshot, test_weakening

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

    def check(self, before: dict[str, str]):
        return check_scope(before, self.work, original_root=self.original)

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


class RefusesCheatingTests(FixCase):
    """Each of these makes CI green without fixing the bug."""

    def test_deleting_the_failing_test_is_refused(self) -> None:
        before = self.ws.before()
        (self.ws.work / "tests" / "test_calc.py").write_text(
            GOOD_TESTS.replace("\n\ndef test_divide():\n    assert divide(6, 3) == 2\n", "\n")
        )

        verdict = self.ws.check(before)

        self.assertFalse(verdict.ok)
        self.assertTrue(any("test functions removed" in p for p in verdict.problems), verdict.problems)

    def test_adding_a_skip_marker_is_refused(self) -> None:
        before = self.ws.before()
        (self.ws.work / "tests" / "test_calc.py").write_text(
            "import pytest\n\n" + GOOD_TESTS.replace("def test_divide():", "@pytest.mark.skip\ndef test_divide():")
        )

        verdict = self.ws.check(before)

        self.assertFalse(verdict.ok)
        self.assertTrue(any("skip" in p for p in verdict.problems), verdict.problems)

    def test_removing_assertions_is_refused(self) -> None:
        before = self.ws.before()
        (self.ws.work / "tests" / "test_calc.py").write_text(
            GOOD_TESTS.replace("    assert divide(6, 3) == 2\n", "    pass\n")
        )

        verdict = self.ws.check(before)

        self.assertFalse(verdict.ok)
        self.assertTrue(any("assertions were removed" in p for p in verdict.problems), verdict.problems)

    def test_touching_the_ci_workflow_is_refused(self) -> None:
        before = self.ws.before()
        workflow = self.ws.work / ".github" / "workflows"
        workflow.mkdir(parents=True)
        (workflow / "tests.yml").write_text("on: []\n")

        verdict = self.ws.check(before)

        self.assertFalse(verdict.ok)
        self.assertTrue(any("out of scope" in p for p in verdict.problems), verdict.problems)

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
        for index in range(12):
            (self.ws.work / "calc" / f"extra_{index}.py").write_text("x = 1\n")

        verdict = self.ws.check(before)

        self.assertFalse(verdict.ok)
        self.assertTrue(any("too many files" in p for p in verdict.problems), verdict.problems)


class TestWeakeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cs-weak-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        for base in ("original", "work"):
            (self.root / base / "tests").mkdir(parents=True)
            (self.root / base / "tests" / "test_x.py").write_text(GOOD_TESTS)

    def test_identical_trees_report_nothing(self) -> None:
        self.assertEqual(test_weakening(self.root / "original", self.root / "work"), [])

    def test_removed_test_is_reported(self) -> None:
        (self.root / "work" / "tests" / "test_x.py").write_text(
            GOOD_TESTS.replace("\n\ndef test_divide():\n    assert divide(6, 3) == 2\n", "\n")
        )
        problems = test_weakening(self.root / "original", self.root / "work")
        self.assertTrue(any("test functions removed" in p for p in problems), problems)


class SnapshotTests(FixCase):
    def test_snapshot_ignores_git_internals(self) -> None:
        (self.ws.work / ".git").mkdir()
        (self.ws.work / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        self.assertNotIn(".git/HEAD", snapshot(self.ws.work))

    def test_snapshot_detects_a_byte_change(self) -> None:
        before = snapshot(self.ws.work)
        path = self.ws.work / "calc" / "__init__.py"
        path.write_text(path.read_text() + "\n# trailing\n")
        self.assertNotEqual(before["calc/__init__.py"], snapshot(self.ws.work)["calc/__init__.py"])


if __name__ == "__main__":
    unittest.main()
