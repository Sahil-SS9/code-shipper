"""STOP C / Phase 4b: merge risk, low-effort detection, review memory.

Each component has one guardrail that is structural rather than advisory, and that is what these tests
mostly check:

* the approvals evaluator **cannot** read the risk score;
* detection defaults off and, when on, can only label and comment — it has no close or block path;
* shadow mode changes no output;
* a non-writer's feedback creates no lesson;
* a reversed disposition withdraws the lesson.
"""

from __future__ import annotations

import ast
import inspect
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from code_shipper.review import effort, memory, risk
from code_shipper.review.verify import Finding


def finding(severity: str = "important", title: str = "a bug") -> Finding:
    return Finding("id", "a.py", 1, severity, title, "body", "+x")


class RiskScoreTests(unittest.TestCase):
    def test_a_small_clean_change_is_low(self) -> None:
        result = risk.score(changed_files=("src/a.py",), lines_changed=12)
        self.assertEqual(result.name, "low")
        self.assertEqual(result.factors, [])

    def test_a_migration_floors_the_rating(self) -> None:
        result = risk.score(changed_files=("migrations/0002_add.py",), lines_changed=5)
        self.assertGreaterEqual(result.level, 3)
        self.assertIn("migration", result.summary())

    def test_auth_code_floors_the_rating(self) -> None:
        result = risk.score(changed_files=("app/auth/login.py",))
        self.assertGreaterEqual(result.level, 3)

    def test_ci_config_floors_the_rating(self) -> None:
        result = risk.score(changed_files=(".github/workflows/ci.yml",))
        self.assertGreaterEqual(result.level, 3)

    def test_a_lockfile_change_is_elevated(self) -> None:
        result = risk.score(changed_files=("package-lock.json",))
        self.assertEqual(result.level, 2)

    def test_size_is_a_factor(self) -> None:
        self.assertGreaterEqual(risk.score(changed_files=("a.py",), lines_changed=900).level, 4)
        self.assertGreaterEqual(risk.score(changed_files=("a.py",), lines_changed=400).level, 2)

    def test_the_rating_is_the_maximum_not_an_average(self) -> None:
        # one severe signal must not be diluted by mild ones
        result = risk.score(changed_files=("migrations/x.py", "a.py"), lines_changed=10)
        self.assertGreaterEqual(result.level, 3)

    def test_important_findings_raise_the_rating(self) -> None:
        result = risk.score(changed_files=("a.py",), findings=[finding("important")])
        self.assertGreaterEqual(result.level, 3)

    def test_nits_do_not_raise_the_rating(self) -> None:
        result = risk.score(changed_files=("a.py",), findings=[finding("nit")])
        self.assertEqual(result.level, 0)

    def test_a_revert_is_a_factor(self) -> None:
        self.assertGreaterEqual(risk.score(changed_files=("a.py",), reverts=True).level, 3)

    def test_every_rating_carries_its_reasons(self) -> None:
        result = risk.score(changed_files=("migrations/x.py",), lines_changed=900)
        self.assertTrue(result.factors)
        for factor in result.factors:
            self.assertTrue(factor.reason, "a factor with no reason is unfalsifiable")

    def test_the_summary_is_readable(self) -> None:
        result = risk.score(changed_files=("migrations/x.py",))
        self.assertIn("Merge risk:", result.summary())
        self.assertIn("migration", result.summary())

    def test_missing_tests_alone_is_only_a_mild_signal(self) -> None:
        result = risk.score(changed_files=("a.py",), lines_changed=100, touches_tests=False)
        self.assertEqual(result.level, 1)


class TheApprovalsEvaluatorCannotReadRiskTests(unittest.TestCase):
    """The guardrail: a risk score that can gate a merge is a second approval system."""

    def test_no_approvals_module_imports_risk(self) -> None:
        import code_shipper.approvals.comment
        import code_shipper.approvals.evaluate
        import code_shipper.approvals.policy
        import code_shipper.approvals.shared

        for module in (code_shipper.approvals.comment, code_shipper.approvals.evaluate,
                       code_shipper.approvals.policy, code_shipper.approvals.shared):
            source = inspect.getsource(module)
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn("risk", alias.name,
                                         f"{module.__name__} imports {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotIn("risk", (node.module or ""),
                                     f"{module.__name__} imports from {node.module}")

    def test_the_evaluator_signature_has_no_risk_parameter(self) -> None:
        from code_shipper.approvals.evaluate import evaluate

        parameters = inspect.signature(evaluate).parameters
        self.assertEqual(set(parameters), {"policy", "facts"},
                         "the evaluator takes the policy and the facts, and nothing advisory")

    def test_the_facts_type_has_no_risk_field(self) -> None:
        from code_shipper.approvals.evaluate import Facts

        self.assertNotIn("risk", Facts.__dataclass_fields__)
        self.assertNotIn("risk_score", Facts.__dataclass_fields__)


class LowEffortTests(unittest.TestCase):
    """D15b: off by default; when on, one label and one note, and nothing else."""

    def test_it_is_off_by_default(self) -> None:
        result = effort.detect(patch="+TODO: implement this\n" * 50)
        self.assertFalse(result.enabled)
        self.assertFalse(result.triggered)
        self.assertEqual(result.label, "")
        self.assertEqual(result.note, "")
        self.assertEqual(result.signals, [], "no signals are even computed when off")

    def test_enabled_and_triggered_produces_exactly_one_label_and_one_note(self) -> None:
        result = effort.detect(
            enabled=True,
            patch="+TODO: implement this\n+placeholder\n",
            commit_message="This commit provides a comprehensive, robust and seamless enhancement",
            lines_changed=500,
            touches_tests=False,
        )
        self.assertTrue(result.triggered)
        self.assertEqual(result.label, effort.LABEL)
        self.assertTrue(result.note)

    def test_it_has_no_way_to_close_block_or_request_changes(self) -> None:
        result = effort.detect(enabled=True, patch="+TODO: implement this\n", lines_changed=500,
                               touches_tests=False)
        actions = result.as_dict()["actions"]
        self.assertFalse(actions["close"])
        self.assertFalse(actions["block"])
        self.assertFalse(actions["request_changes"])
        self.assertEqual(set(actions), {"label", "note", "close", "block", "request_changes"})

    def test_the_module_contains_no_close_or_block_call(self) -> None:
        source = inspect.getsource(effort)
        for forbidden in ('"close"', "pr.close", "close_pr", "change_status", "request_changes("):
            if forbidden in ('"close"',):
                # the only permitted appearance is the False in the actions dict
                self.assertNotIn('"close": True', source)
                continue
            self.assertNotIn(forbidden, source,
                             f"{forbidden} must not appear in the detection module")

    def test_the_note_does_not_claim_authorship(self) -> None:
        result = effort.detect(
            enabled=True,
            patch="+TODO: implement this\n+placeholder\n",
            commit_message="comprehensive robust seamless best practices",
            lines_changed=500,
            touches_tests=False,
        )
        self.assertIn("say nothing about authorship", result.note)
        self.assertIn("not proof", result.note)

    def test_the_note_says_it_blocks_nothing(self) -> None:
        result = effort.detect(
            enabled=True, patch="+TODO: implement this\n+placeholder\n",
            commit_message="comprehensive robust seamless",
            lines_changed=500, touches_tests=False,
        )
        self.assertIn("nothing here blocks", result.note)

    def test_a_normal_change_does_not_trigger(self) -> None:
        result = effort.detect(enabled=True, patch="+x = compute(a, b)\n" * 5,
                               commit_message="Fix the divide function", lines_changed=8,
                               touches_tests=True)
        self.assertFalse(result.triggered)

    def test_one_weak_signal_alone_does_not_trigger(self) -> None:
        result = effort.detect(enabled=True, lines_changed=500, touches_tests=False)
        self.assertFalse(result.triggered, "a large diff with no tests is not enough on its own")

    def test_signals_are_named_in_the_note(self) -> None:
        result = effort.detect(
            enabled=True, patch="+TODO: implement this\n+placeholder\n",
            commit_message="comprehensive robust seamless", lines_changed=500, touches_tests=False,
        )
        for signal in result.signals:
            self.assertIn(signal.detail, result.note)


class MemoryTests(unittest.TestCase):
    """D15c: lessons from write-access feedback only, shadow by default, withdrawable."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cs-lessons-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.store = memory.Store(self.root)
        self.addCleanup(lambda: self.root.exists() and None)

    def _feedback(self, disposition="agreed", permission="write", author="alice", **kwargs):
        return memory.Feedback(
            finding_fingerprint="fp-1", disposition=disposition, author=author,
            body="this mattered, please check it every time", permission=permission, **kwargs
        )

    def test_feedback_from_a_writer_creates_a_lesson(self) -> None:
        result = memory.learn(self.store, self._feedback())
        self.assertTrue(result["accepted"])
        self.assertEqual(len(self.store.load()), 1)

    def test_feedback_from_a_non_writer_creates_no_lesson(self) -> None:
        result = memory.learn(self.store, self._feedback(permission="read", author="bob"))
        self.assertFalse(result["accepted"])
        self.assertEqual(self.store.load(), [])
        self.assertIn("write access", result["reason"])

    def test_the_non_writer_refusal_is_reported_not_silent(self) -> None:
        result = memory.learn(self.store, self._feedback(permission="none", author="mallory"))
        self.assertTrue(result["reason"], "a dropped contribution must say why")

    def test_mode_off_learns_nothing(self) -> None:
        result = memory.learn(self.store, self._feedback(), mode="off")
        self.assertFalse(result["accepted"])
        self.assertEqual(self.store.load(), [])

    def test_shadow_mode_stores_but_changes_no_output(self) -> None:
        memory.learn(self.store, self._feedback(), mode="shadow")
        self.assertEqual(len(self.store.load()), 1, "the lesson is stored")
        self.assertEqual(memory.apply_to_prompt(self.store, mode="shadow"), "",
                         "shadow means it changes no posted output")

    def test_off_mode_also_changes_no_output(self) -> None:
        memory.learn(self.store, self._feedback(), mode="shadow")
        self.assertEqual(memory.apply_to_prompt(self.store, mode="off"), "")

    def test_on_mode_applies_the_lesson(self) -> None:
        memory.learn(self.store, self._feedback(), mode="on")
        prompt = memory.apply_to_prompt(self.store, mode="on")
        # The header belongs to the engine, which frames the block: printing it here as well showed the
        # reviewer the same sentence twice. The rendered-prompt test for it is in
        # tests/test_memory_wired.py::TheStoreIsAFileSoTheReadPathFiltersTooTests.
        self.assertNotIn("Lessons from feedback", prompt, "the header is the engine's, not the read path's")
        self.assertIn("report: fp-1", prompt)
        self.assertIn("because:", prompt, "a lesson must carry its evidence")

    def test_a_lesson_can_be_withdrawn(self) -> None:
        result = memory.learn(self.store, self._feedback(), mode="on")
        lesson_id = result["lesson"]["id"]
        self.assertIn("report: fp-1", memory.apply_to_prompt(self.store, mode="on"))
        self.assertTrue(self.store.withdraw(lesson_id))
        self.assertNotIn("fp-1", memory.apply_to_prompt(self.store, mode="on"))

    def test_a_reversed_disposition_withdraws_automatically(self) -> None:
        # the case that matters: a lesson from a mistaken "you missed this" must not outlive the
        # correction
        memory.learn(self.store, self._feedback(target_cue="fp-1"), mode="on")
        self.assertEqual(len(memory.list_lessons(self.store)), 1)

        memory.learn(self.store, self._feedback(disposition="disagreed", target_cue="fp-1"), mode="on")

        remaining = memory.list_lessons(self.store)
        self.assertEqual(remaining, [], "the reversal must withdraw the lesson")

    def test_withdrawn_lessons_can_still_be_listed(self) -> None:
        result = memory.learn(self.store, self._feedback(), mode="on")
        self.store.withdraw(result["lesson"]["id"])
        self.assertEqual(memory.list_lessons(self.store), [])
        self.assertEqual(len(memory.list_lessons(self.store, include_withdrawn=True)), 1)

    def test_the_same_lesson_is_not_duplicated(self) -> None:
        memory.learn(self.store, self._feedback(), mode="on")
        memory.learn(self.store, self._feedback(), mode="on")
        self.assertEqual(len(self.store.load()), 1, "the same cue and action is one lesson")

    def test_lessons_are_stored_as_plain_json(self) -> None:
        # harness- and provider-neutral: no Hermes, no memory-provider API
        memory.learn(self.store, self._feedback(), mode="on")
        data = json.loads((self.root / "review-lessons.json").read_text())
        self.assertIn("lessons", data)
        self.assertIn("cue", data["lessons"][0])
        self.assertIn("action", data["lessons"][0])
        self.assertIn("evidence", data["lessons"][0])

    def test_a_lesson_never_touches_approvals(self) -> None:
        source = inspect.getsource(memory)
        tree = ast.parse(source)
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        self.assertFalse([name for name in imported if ".approvals" in name],
                         "lessons must never reach the approvals policy or security settings")

    def test_a_corrupt_lessons_file_is_an_empty_list_not_a_crash(self) -> None:
        (self.root / "review-lessons.json").write_text("{not json")
        self.assertEqual(self.store.load(), [])

    def test_an_unknown_mode_is_refused(self) -> None:
        result = memory.learn(self.store, self._feedback(), mode="sometimes")
        self.assertFalse(result["accepted"])
        self.assertIn("unknown mode", result["reason"])


if __name__ == "__main__":
    unittest.main()
