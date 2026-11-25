"""P3-B part 2: classify, contained workspace, and the fix wiring.

The wiring tests use a stand-in worker so they run everywhere; the real container run is separate and
reported as a receipt. What these prove is the *order*: scope is judged from disk after the fix and
before the push, and a failed check stops the run rather than narrowing it.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from code_shipper.budget import RunBudget
from code_shipper.steward import classify
from code_shipper.steward.classify import WorkItem, WorkKind
from code_shipper.steward.fix import build_prompt, run_fix
from code_shipper.steward.policy import ScopePolicy
from code_shipper.steward.workspace import WorkspaceError, prepare, require_private_dir

GOOD_SOURCE = """def divide(a, b):
    if b == 0:
        raise ZeroDivisionError("b must not be zero")
    return a / b
"""
BUGGY_SOURCE = GOOD_SOURCE.replace("a / b", "a * b")
GOOD_TESTS = """from calc import divide


def test_divide():
    assert divide(6, 3) == 2
"""

POLICY = ScopePolicy(allowed=("calc/**/*.py", "tests/**/*.py"), forbidden=("**/conftest.py",))


@dataclass
class FakeCheck:
    name: str
    conclusion: str
    log_tail: str = ""
    files: list | None = None


@dataclass
class FakeComment:
    id: int
    body: str
    path: str = ""


@dataclass
class FakeFinding:
    title: str
    severity: str
    path: str
    fingerprint: str = ""


@dataclass
class FakePr:
    number: int = 18


class ClassifyTests(unittest.TestCase):
    """FR-S1: four kinds of work, and a plain statement when there is none."""

    def test_a_failing_check_caused_by_this_change_is_work(self) -> None:
        check = FakeCheck("tests", "failure", log_tail="FAILED calc/__init__.py::test_divide")
        item = classify.classify_failing_check(check, ["calc/__init__.py"])
        self.assertEqual(item.kind, WorkKind.FAILING_CHECK)
        self.assertTrue(item.actionable)
        self.assertIn("calc/__init__.py", item.paths)

    def test_a_failing_check_on_untouched_code_is_not_work(self) -> None:
        # the realistic false positive: red CI, different cause
        check = FakeCheck("tests", "failure", log_tail="FAILED vendor/lib.py::test_x")
        item = classify.classify_failing_check(check, ["calc/__init__.py"])
        self.assertEqual(item.kind, WorkKind.NONE)
        self.assertFalse(item.actionable)
        self.assertIn("outside this change", item.reason)

    def test_a_passing_check_is_not_work(self) -> None:
        for conclusion in ("success", "neutral", "skipped"):
            with self.subTest(conclusion=conclusion):
                item = classify.classify_failing_check(
                    FakeCheck("tests", conclusion), ["calc/__init__.py"]
                )
                self.assertEqual(item.kind, WorkKind.NONE)

    def test_a_conflict_is_work(self) -> None:
        item = classify.classify_conflict(pr=FakePr(), mergeable_state="dirty")
        self.assertEqual(item.kind, WorkKind.CONFLICT)
        self.assertTrue(item.actionable)

    def test_a_clean_merge_state_is_not_work(self) -> None:
        self.assertEqual(
            classify.classify_conflict(pr=FakePr(), mergeable_state="clean").kind, WorkKind.NONE
        )

    def test_a_specific_change_request_is_work(self) -> None:
        comment = FakeComment(1, "Please use <= here so negatives raise too.", "calc/__init__.py")
        item = classify.classify_review_comment(comment, ["calc/__init__.py"])
        self.assertEqual(item.kind, WorkKind.REVIEW_COMMENT)
        self.assertTrue(item.actionable)

    def test_a_question_becomes_a_reply_not_a_fix(self) -> None:
        # FR-S1: reply when the request is a question rather than a change
        comment = FakeComment(2, "Why is the guard b == 0 rather than b <= 0?", "calc/__init__.py")
        item = classify.classify_review_comment(comment, ["calc/__init__.py"])
        self.assertTrue(item.reply_only)
        self.assertFalse(item.actionable)

    def test_a_design_level_request_becomes_a_reply(self) -> None:
        comment = FakeComment(3, "Please refactor this whole module into smaller functions.",
                              "calc/__init__.py")
        item = classify.classify_review_comment(comment, ["calc/__init__.py"])
        self.assertTrue(item.reply_only)
        self.assertIn("design-level", item.reason)

    def test_a_comment_on_an_untouched_file_is_not_work(self) -> None:
        comment = FakeComment(4, "Please fix this.", "unrelated/file.py")
        item = classify.classify_review_comment(comment, ["calc/__init__.py"])
        self.assertEqual(item.kind, WorkKind.NONE)

    def test_a_question_phrased_as_an_instruction_is_work(self) -> None:
        comment = FakeComment(5, "Why not just clamp it to zero instead?", "calc/__init__.py")
        item = classify.classify_review_comment(comment, ["calc/__init__.py"])
        self.assertTrue(item.actionable, item.reason)

    def test_an_important_finding_is_work(self) -> None:
        item = classify.classify_finding(FakeFinding("divide multiplies", "important", "calc/__init__.py"))
        self.assertEqual(item.kind, WorkKind.IMPORTANT_FINDING)
        self.assertTrue(item.actionable)

    def test_a_nit_is_not_work(self) -> None:
        item = classify.classify_finding(FakeFinding("rename this", "nit", "calc/__init__.py"))
        self.assertEqual(item.kind, WorkKind.NONE)

    def test_nothing_to_do_is_stated_rather_than_empty(self) -> None:
        items = classify.classify(
            pr=FakePr(), mergeable_state="clean", checks=[FakeCheck("t", "success")],
            changed_files=["calc/__init__.py"],
        )
        # each check's own verdict is kept, plus one summary — never an empty list
        self.assertGreaterEqual(len(items), 1)
        self.assertEqual(classify.actionable(items), [])
        reasons = " ".join(item.reason for item in items)
        self.assertIn("not failing", reasons)
        self.assertIn("nothing to do", reasons)

    def test_actionable_filters_without_deleting(self) -> None:
        items = [
            WorkItem(WorkKind.NONE, "nothing"),
            WorkItem(WorkKind.REVIEW_COMMENT, "reply", reply_only=True),
            WorkItem(WorkKind.FAILING_CHECK, "real work"),
        ]
        self.assertEqual([i.kind for i in classify.actionable(items)], [WorkKind.FAILING_CHECK])
        self.assertEqual(len(items), 3)


class WorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cs-ws-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.repo = self.root / "repo"
        (self.repo / "calc").mkdir(parents=True)
        (self.repo / "tests").mkdir()
        (self.repo / "calc" / "__init__.py").write_text(BUGGY_SOURCE)
        (self.repo / "tests" / "test_calc.py").write_text(GOOD_TESTS)
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                    ["git", "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "base"]):
            subprocess.run(cmd, cwd=str(self.repo), check=True, capture_output=True)
        self.sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(self.repo),
            capture_output=True, text=True,
        ).stdout.strip()

    def test_the_workspace_has_an_editable_copy_and_a_pristine_one(self) -> None:
        ws = prepare(self.repo, base_sha=self.sha, head_sha=self.sha, branch="feature",
                     staging_root=self.root / "staging")
        self.addCleanup(ws.cleanup)
        self.assertTrue((ws.work / "calc" / "__init__.py").exists())
        self.assertTrue((ws.pristine / "calc" / "__init__.py").exists())
        self.assertEqual(ws.branch, "feature")
        self.assertIn("calc/__init__.py", ws.files)

    def test_the_run_directory_is_private(self) -> None:
        ws = prepare(self.repo, base_sha=self.sha, head_sha=self.sha, branch="feature",
                     staging_root=self.root / "staging")
        self.addCleanup(ws.cleanup)
        mode = stat.S_IMODE(ws.root.stat().st_mode)
        self.assertEqual(mode & 0o077, 0, f"run dir is {oct(mode)}, must be 0700")

    def test_a_widened_run_directory_is_refused(self) -> None:
        ws = prepare(self.repo, base_sha=self.sha, head_sha=self.sha, branch="feature",
                     staging_root=self.root / "staging")
        self.addCleanup(ws.cleanup)
        os.chmod(ws.root, 0o755)
        with self.assertRaises(WorkspaceError) as caught:
            require_private_dir(ws.root)
        self.assertIn("0700", str(caught.exception))

    def test_the_editing_copy_does_not_share_git_objects_with_the_source(self) -> None:
        ws = prepare(self.repo, base_sha=self.sha, head_sha=self.sha, branch="feature",
                     staging_root=self.root / "staging")
        self.addCleanup(ws.cleanup)
        source_objects = (self.repo / ".git" / "objects").resolve()
        work_objects = (ws.work / ".git" / "objects").resolve()
        self.assertNotEqual(source_objects, work_objects)

    def test_a_failed_prepare_leaves_nothing_behind(self) -> None:
        staging = self.root / "staging"
        staging.mkdir(exist_ok=True)
        before = set(staging.iterdir())
        with self.assertRaises(Exception):
            prepare(self.root / "does-not-exist", base_sha="0" * 40, head_sha="0" * 40,
                    branch="x", staging_root=staging)
        self.assertEqual(set(staging.iterdir()), before)

    def test_the_worker_can_write_its_copy(self) -> None:
        # the container runs as a different uid, so the copy must be writable by others
        ws = prepare(self.repo, base_sha=self.sha, head_sha=self.sha, branch="feature",
                     staging_root=self.root / "staging")
        self.addCleanup(ws.cleanup)
        mode = stat.S_IMODE((ws.work / "calc" / "__init__.py").stat().st_mode)
        self.assertTrue(mode & stat.S_IWOTH, f"file is {oct(mode)}, worker could not write it")


class WiringTests(unittest.TestCase):
    """The order of operations is the security property."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cs-fix-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.repo = self.root / "repo"
        (self.repo / "calc").mkdir(parents=True)
        (self.repo / "tests").mkdir()
        (self.repo / "calc" / "__init__.py").write_text(BUGGY_SOURCE)
        (self.repo / "tests" / "test_calc.py").write_text(GOOD_TESTS)
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                    ["git", "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "base"]):
            subprocess.run(cmd, cwd=str(self.repo), check=True, capture_output=True)
        self.sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(self.repo),
            capture_output=True, text=True,
        ).stdout.strip()

    def run_fix(self, worker, *, push=False, policy=POLICY, item=None):
        return run_fix(
            items=[item or WorkItem(WorkKind.FAILING_CHECK, "the divide test fails")],
            repo_dir=self.repo,
            base_sha=self.sha,
            head_sha=self.sha,
            head_branch="feature",
            worker=worker,
            policy=policy,
            budget=RunBudget(fix_rounds=1, pushes_per_day=1, wall_clock_seconds=60),
            token="ghp_fake" if push else None,
            staging_root=self.root / "staging",
            push=push,
        )

    def test_a_good_fix_is_in_scope_and_not_pushed_in_dry_run(self) -> None:
        class Fixer:
            def run(self, *, workspace, item, prompt, budget):
                (workspace.work / "calc" / "__init__.py").write_text(GOOD_SOURCE)
                return {"changed": ["calc/__init__.py"]}

        receipt = self.run_fix(Fixer())
        self.assertTrue(receipt.ok, receipt.problems)
        self.assertEqual(receipt.verdict, "in_scope_not_pushed")
        self.assertEqual(receipt.changed, ["calc/__init__.py"])

    def test_deleting_a_test_is_refused_before_any_push(self) -> None:
        class Cheat:
            def run(self, *, workspace, item, prompt, budget):
                (workspace.work / "tests" / "test_calc.py").write_text("")
                return {}

        receipt = self.run_fix(Cheat(), push=True)
        self.assertFalse(receipt.ok)
        self.assertEqual(receipt.stage, "scope")
        self.assertEqual(receipt.verdict, "out_of_scope")
        self.assertTrue(any("test removed" in p for p in receipt.problems), receipt.problems)
        self.assertEqual(receipt.push, {}, "a refused scope must not reach the push stage")

    def test_editing_outside_the_policy_is_refused(self) -> None:
        class WrongFile:
            def run(self, *, workspace, item, prompt, budget):
                (workspace.work / ".github").mkdir(exist_ok=True)
                (workspace.work / ".github" / "workflows.yml").write_text("on: []\n")
                return {}

        receipt = self.run_fix(WrongFile(), push=True)
        self.assertFalse(receipt.ok)
        self.assertTrue(any("out of scope" in p for p in receipt.problems), receipt.problems)
        self.assertEqual(receipt.push, {})

    def test_a_fix_that_changes_nothing_is_refused(self) -> None:
        class DoNothing:
            def run(self, *, workspace, item, prompt, budget):
                return {}

        receipt = self.run_fix(DoNothing())
        self.assertFalse(receipt.ok)
        self.assertIn("no change was made", receipt.problems)

    def test_a_worker_failure_does_not_escape(self) -> None:
        class Broken:
            def run(self, *, workspace, item, prompt, budget):
                raise RuntimeError("the harness died")

        receipt = self.run_fix(Broken())
        self.assertFalse(receipt.ok)
        self.assertEqual(receipt.verdict, "worker_failed")
        self.assertIn("the harness died", receipt.problems[0])

    def test_nothing_to_do_is_reported_not_run(self) -> None:
        class ShouldNotRun:
            def run(self, *, workspace, item, prompt, budget):
                raise AssertionError("the worker must not run when there is nothing to do")

        receipt = run_fix(
            items=[WorkItem(WorkKind.NONE, "nothing to do")],
            repo_dir=self.repo, base_sha=self.sha, head_sha=self.sha, head_branch="feature",
            worker=ShouldNotRun(), policy=POLICY,
            budget=RunBudget(fix_rounds=1), push=False, staging_root=self.root / "staging",
        )
        self.assertTrue(receipt.ok)
        self.assertEqual(receipt.verdict, "nothing_to_do")

    def test_the_budget_stops_a_second_round(self) -> None:
        class Fixer:
            def run(self, *, workspace, item, prompt, budget):
                (workspace.work / "calc" / "__init__.py").write_text(GOOD_SOURCE)
                return {}

        budget = RunBudget(fix_rounds=1)
        budget.start_round()
        receipt = run_fix(
            items=[WorkItem(WorkKind.FAILING_CHECK, "x")],
            repo_dir=self.repo, base_sha=self.sha, head_sha=self.sha, head_branch="feature",
            worker=Fixer(), policy=POLICY, budget=budget, push=False,
            staging_root=self.root / "staging",
        )
        self.assertFalse(receipt.ok)
        self.assertEqual(receipt.verdict, "budget_exhausted")

    def test_the_workspace_is_cleaned_up_even_on_success(self) -> None:
        staging = self.root / "staging"

        class Fixer:
            def run(self, *, workspace, item, prompt, budget):
                (workspace.work / "calc" / "__init__.py").write_text(GOOD_SOURCE)
                return {}

        self.run_fix(Fixer())
        leftovers = [p for p in staging.iterdir()] if staging.exists() else []
        self.assertEqual(leftovers, [], f"left behind: {leftovers}")

    def test_the_receipt_records_the_scope_policy_used(self) -> None:
        class Fixer:
            def run(self, *, workspace, item, prompt, budget):
                (workspace.work / "calc" / "__init__.py").write_text(GOOD_SOURCE)
                return {}

        receipt = self.run_fix(Fixer())
        self.assertIn("calc/**/*.py", receipt.scope_policy["allowed"])
        self.assertEqual(receipt.scope_policy["max_files"], 10)

    def test_a_push_without_a_token_is_refused(self) -> None:
        class Fixer:
            def run(self, *, workspace, item, prompt, budget):
                (workspace.work / "calc" / "__init__.py").write_text(GOOD_SOURCE)
                return {}

        receipt = run_fix(
            items=[WorkItem(WorkKind.FAILING_CHECK, "x")],
            repo_dir=self.repo, base_sha=self.sha, head_sha=self.sha, head_branch="feature",
            worker=Fixer(), policy=POLICY, budget=RunBudget(fix_rounds=1),
            token=None, push=True, staging_root=self.root / "staging",
        )
        self.assertFalse(receipt.ok)
        self.assertEqual(receipt.verdict, "no_token")


class PromptTests(unittest.TestCase):
    def test_the_prompt_says_nothing_about_credentials_or_the_broker(self) -> None:
        prompt = build_prompt(WorkItem(WorkKind.FAILING_CHECK, "the test fails"), head_branch="f")
        for forbidden in ("token", "broker", "/run/cs", "credential", "secret"):
            self.assertNotIn(forbidden, prompt.lower())

    def test_the_prompt_forbids_weakening_tests(self) -> None:
        prompt = build_prompt(WorkItem(WorkKind.FAILING_CHECK, "the test fails"), head_branch="f")
        self.assertIn("Do not weaken, skip or delete tests", prompt)

    def test_the_prompt_does_not_list_paths(self) -> None:
        # a worker taught to expect a path list underperforms the first time it does not get one
        item = WorkItem(WorkKind.FAILING_CHECK, "the test fails", paths=["calc/__init__.py"])
        self.assertNotIn("calc/__init__.py", build_prompt(item, head_branch="f"))


if __name__ == "__main__":
    unittest.main()
