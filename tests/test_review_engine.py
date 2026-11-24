"""Review engine tests: lens selection, verification gating, ranking, and T-ADV-05.

The property that matters most is the one T-ADV-05 describes: nothing is reported that has not
survived verification, and an incomplete review says so rather than reporting "no issues". A review
that silently reports zero findings because a lens crashed is worse than one that fails loudly.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from code_shipper.harness.base import AgentResult, AgentTask
from code_shipper.hosts.base import Diff, FilePatch
from code_shipper.review import engine
from code_shipper.review.lenses import LENS_COUNT, LENSES, select
from code_shipper.review.rank import MAX_IMPORTANT, MAX_NIT, dedupe, plan, rank
from code_shipper.review.verify import Finding, Verdict, apply_verdicts, batch, parse_verdicts

DIFF = Diff(
    files=(
        FilePatch(
            path="calc/__init__.py",
            patch="@@ -5,4 +5,4 @@\n def divide(a, b):\n-    return a / b\n+    return a * b\n",
        ),
    )
)


def finding(ident="f1", path="calc/__init__.py", line=7, severity="important", title="Wrong operator"):
    return Finding(
        id=ident, path=path, line=line, severity=severity,
        title=title, body="divide multiplies", evidence="+    return a * b",
    )


class ScriptedHarness:
    """A harness that answers each task kind from a script. No process, no network."""

    name = "scripted"
    supported_kinds = frozenset({"review_lens", "verify", "fix", "reply"})

    def __init__(self, *, lens_outputs=None, verify_outputs=None, lens_error=None):
        self.lens_outputs = lens_outputs or {}
        self.verify_outputs = verify_outputs or []
        self.lens_error = lens_error
        self.tasks: list[AgentTask] = []

    def available(self):
        return True, "scripted"

    def run(self, task, env):
        self.tasks.append(task)
        if task.kind == "review_lens":
            if self.lens_error is not None:
                return AgentResult(ok=False, error=self.lens_error)
            for name, output in self.lens_outputs.items():
                if f"Your lens is: {LENSES[name].title}." in task.prompt:
                    return AgentResult(ok=True, output=output)
            return AgentResult(ok=True, output={"findings": []})
        if task.kind == "verify":
            index = len([t for t in self.tasks if t.kind == "verify"]) - 1
            output = self.verify_outputs[index] if index < len(self.verify_outputs) else {"verdicts": []}
            return AgentResult(ok=True, output=output)
        return AgentResult(ok=False, error="unsupported")


class LensSelectionTests(unittest.TestCase):
    def test_quick_is_one_combined_lens_not_a_truncated_deep_one(self) -> None:
        selected = select("quick")
        self.assertEqual([lens.name for lens in selected], ["combined"])

    def test_standard_is_three(self) -> None:
        self.assertEqual(len(select("standard")), 3)

    def test_deep_is_five(self) -> None:
        self.assertEqual([lens.name for lens in select("deep")],
                         ["correctness", "security", "edge", "tests", "contract"])

    def test_counts_match_k7(self) -> None:
        self.assertEqual(LENS_COUNT, {"quick": 1, "standard": 3, "deep": 5})

    def test_an_unknown_level_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            select("thorough")


class VerificationTests(unittest.TestCase):
    """FR-R2: verification runs at every level, and only confirmed findings are posted."""

    def test_batch_size_is_at_most_ten(self) -> None:
        many = [finding(ident=f"f{i}") for i in range(25)]
        groups = batch(many)
        self.assertEqual([len(g) for g in groups], [10, 10, 5])

    def test_batching_an_empty_list_is_empty(self) -> None:
        self.assertEqual(batch([]), [])

    def test_only_confirmed_findings_survive(self) -> None:
        found = [finding(ident="a"), finding(ident="b"), finding(ident="c")]
        verdicts = [
            Verdict("a", "confirmed"),
            Verdict("b", "refuted"),
            Verdict("c", "unsure"),
        ]
        kept = apply_verdicts(found, verdicts)
        self.assertEqual([f.id for f in kept], ["a"])

    def test_unsure_is_not_posted(self) -> None:
        # the safe direction: uncertainty must not become a posted claim
        kept = apply_verdicts([finding(ident="a")], [Verdict("a", "unsure")])
        self.assertEqual(kept, [])

    def test_a_finding_with_no_verdict_is_dropped(self) -> None:
        # silence from the verifier is not confirmation
        kept = apply_verdicts([finding(ident="a")], [])
        self.assertEqual(kept, [])

    def test_the_prompt_asks_to_refute(self) -> None:
        prompt = engine.build_prompt([finding()], "diff here") if hasattr(engine, "build_prompt") else None
        if prompt is None:
            from code_shipper.review.verify import build_prompt

            prompt = build_prompt([finding()], "diff here")
        self.assertIn("REFUTE", prompt)
        self.assertIn("untrusted", prompt)

    def test_verdicts_that_are_malformed_are_ignored(self) -> None:
        parsed = parse_verdicts(
            {"verdicts": [{"id": "a", "verdict": "confirmed"}, {"id": "b"}, {"nope": 1}, "junk"]}
        )
        self.assertEqual([v.id for v in parsed], ["a"])


class RankingTests(unittest.TestCase):
    def test_important_sorts_before_nit(self) -> None:
        ordered = rank([finding(ident="n", severity="nit"), finding(ident="i", severity="important")])
        self.assertEqual([f.severity for f in ordered], ["important", "nit"])

    def test_within_a_severity_sorts_by_path_then_line(self) -> None:
        ordered = rank(
            [
                finding(ident="c", path="b.py", line=1),
                finding(ident="b", path="a.py", line=9),
                finding(ident="a", path="a.py", line=2),
            ]
        )
        self.assertEqual([f.id for f in ordered], ["a", "b", "c"])

    def test_duplicates_at_the_same_place_are_merged(self) -> None:
        merged = dedupe([finding(ident="a"), finding(ident="b")])  # same path/line/title
        self.assertEqual(len(merged), 1)

    def test_the_same_place_with_a_different_claim_is_kept(self) -> None:
        merged = dedupe([finding(ident="a"), finding(ident="b", title="Different problem")])
        self.assertEqual(len(merged), 2)

    def test_the_inline_cap_is_enforced_and_the_rest_counted(self) -> None:
        many = [finding(ident=f"f{i}", line=i + 1) for i in range(MAX_IMPORTANT + 5)]
        result = plan(many)
        self.assertEqual(len([f for f in result.to_post if f.severity == "important"]), MAX_IMPORTANT)
        self.assertEqual(result.omitted_important, 5)

    def test_nits_are_capped_separately(self) -> None:
        many = [finding(ident=f"n{i}", line=i + 1, severity="nit") for i in range(MAX_NIT + 3)]
        result = plan(many)
        self.assertEqual(len([f for f in result.to_post if f.severity == "nit"]), MAX_NIT)
        self.assertEqual(result.omitted_nit, 3)

    def test_the_summary_line_reports_what_was_omitted(self) -> None:
        many = [finding(ident=f"f{i}", line=i + 1) for i in range(MAX_IMPORTANT + 2)]
        self.assertIn("2 more important", plan(many).summary_line())

    def test_no_omissions_means_no_summary_line(self) -> None:
        self.assertEqual(plan([finding()]).summary_line(), "")


class EngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="cs-engine-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))

    def review(self, harness, level="standard"):
        return engine.review(diff=DIFF, harness=harness, level=level, workdir=self.dir, env={})

    def test_a_confirmed_finding_reaches_the_plan(self) -> None:
        harness = ScriptedHarness(
            lens_outputs={
                "correctness": {"findings": [
                    {"path": "calc/__init__.py", "line": 7, "severity": "important",
                     "title": "divide multiplies", "body": "b", "evidence": "+ return a * b"}
                ]}
            },
            verify_outputs=[{"verdicts": [{"id": "PLACEHOLDER", "verdict": "confirmed"}]}],
        )
        # give the verifier the real id: it is derived from path/line/title
        expected = engine._finding_id("correctness", "calc/__init__.py", 7, "divide multiplies")
        harness.verify_outputs = [{"verdicts": [{"id": expected, "verdict": "confirmed"}]}]

        outcome = self.review(harness)

        self.assertTrue(outcome.complete)
        self.assertEqual(outcome.raw_findings, 1)
        self.assertEqual(outcome.confirmed, 1)
        self.assertEqual(len(outcome.findings), 1)
        self.assertEqual(outcome.findings[0].title, "divide multiplies")

    def test_a_refuted_finding_is_not_reported(self) -> None:
        expected = engine._finding_id("correctness", "calc/__init__.py", 7, "divide multiplies")
        harness = ScriptedHarness(
            lens_outputs={
                "correctness": {"findings": [
                    {"path": "calc/__init__.py", "line": 7, "severity": "important",
                     "title": "divide multiplies", "body": "b", "evidence": "e"}
                ]}
            },
            verify_outputs=[{"verdicts": [{"id": expected, "verdict": "refuted"}]}],
        )
        outcome = self.review(harness)
        self.assertEqual(outcome.raw_findings, 1)
        self.assertEqual(outcome.confirmed, 0)
        self.assertEqual(outcome.findings, [])
        self.assertEqual(outcome.dropped_unverified, 1)

    def test_quick_still_verifies(self) -> None:
        expected = engine._finding_id("combined", "calc/__init__.py", 7, "divide multiplies")
        harness = ScriptedHarness(
            lens_outputs={
                "combined": {"findings": [
                    {"path": "calc/__init__.py", "line": 7, "severity": "important",
                     "title": "divide multiplies", "body": "b", "evidence": "e"}
                ]}
            },
            verify_outputs=[{"verdicts": [{"id": expected, "verdict": "confirmed"}]}],
        )
        outcome = self.review(harness, level="quick")

        self.assertEqual(outcome.lenses_run, ["combined"])
        verify_tasks = [t for t in harness.tasks if t.kind == "verify"]
        self.assertEqual(len(verify_tasks), 1, "Quick must still run verification")

    def test_a_failed_lens_marks_the_review_incomplete(self) -> None:
        harness = ScriptedHarness(lens_error="model unavailable")
        outcome = self.review(harness)
        self.assertFalse(outcome.complete)
        self.assertTrue(outcome.errors)
        # and it must not silently look like a clean review
        self.assertEqual(outcome.findings, [])

    def test_a_review_with_no_findings_is_complete(self) -> None:
        outcome = self.review(ScriptedHarness())
        self.assertTrue(outcome.complete)
        self.assertEqual(outcome.findings, [])

    def test_a_failed_verification_batch_drops_its_findings(self) -> None:
        """T-ADV-05: unverified findings are never posted."""
        harness = ScriptedHarness(
            lens_outputs={
                "correctness": {"findings": [
                    {"path": "calc/__init__.py", "line": 7, "severity": "important",
                     "title": "t", "body": "b", "evidence": "e"}
                ]}
            },
            verify_outputs=[],  # empty verdicts: nothing is confirmed
        )
        outcome = self.review(harness)
        self.assertEqual(outcome.findings, [])
        self.assertEqual(outcome.dropped_unverified, 1)

    def test_standard_runs_three_lenses_and_deep_five(self) -> None:
        self.assertEqual(len(self.review(ScriptedHarness(), "standard").lenses_run), 3)
        self.assertEqual(len(self.review(ScriptedHarness(), "deep").lenses_run), 5)

    def test_malformed_findings_are_dropped_not_guessed(self) -> None:
        harness = ScriptedHarness(
            lens_outputs={
                "correctness": {"findings": [
                    {"path": "", "line": 1, "title": "t"},
                    {"path": "a.py", "line": "seven", "title": "t"},
                    {"path": "a.py", "line": 1, "title": ""},
                    {"path": "calc/__init__.py", "line": 3, "title": "real", "severity": "nit"},
                ]}
            }
        )
        outcome = self.review(harness)
        self.assertEqual(outcome.raw_findings, 1)


class DiffRenderingTests(unittest.TestCase):
    def test_a_truncated_diff_says_so(self) -> None:
        big = Diff(files=tuple(FilePatch(path=f"f{i}.py", patch="x" * 500) for i in range(200)))
        text = engine.diff_text(big, limit=2000)
        self.assertIn("truncated", text)

    def test_an_empty_diff_is_labelled(self) -> None:
        self.assertIn("empty", engine.diff_text(Diff()))

    def test_ci_log_is_fenced_when_present(self) -> None:
        prompt = engine.lens_prompt(LENSES["correctness"], DIFF, log_tail="FAILED test_x")
        self.assertIn('<untrusted source="ci_log">', prompt)
        self.assertIn("FAILED test_x", prompt)


if __name__ == "__main__":
    unittest.main()
