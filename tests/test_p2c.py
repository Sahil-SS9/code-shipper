"""P2-C tests: stable fingerprint, review state, verdict block, cache, wrap-up reserve, path filters.

The fingerprint tests carry the weight. D11's acceptance test is "shifting the code by N lines does
not re-post the same finding", and the two-run comparison below is a real measurement rather than a
synthetic pair: the two receipts are actual model output over identical code.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from code_shipper.review.cache import ReviewCache
from code_shipper.review.filters import PathFilter
from code_shipper.review.fingerprint import (
    Fingerprinted,
    dedupe_by_fingerprint,
    fingerprint,
    new_since,
    normalise_evidence,
    resolved_since,
)
from code_shipper.review.report import ReviewReport, ReviewState

EVIDENCE_DIR = Path(__file__).resolve().parent.parent / "docs/reports/evidence/P2"


class FingerprintStabilityTests(unittest.TestCase):
    def test_the_line_number_is_not_part_of_identity(self) -> None:
        # D11's acceptance test, by construction: there is no line input at all
        one = fingerprint(path="calc/__init__.py", title="t", evidence="+ return a * b")
        two = fingerprint(path="calc/__init__.py", title="t", evidence="+ return a * b")
        self.assertEqual(one, two)

    def test_shifting_the_code_does_not_change_the_fingerprint(self) -> None:
        # the same finding, reported at line 6 and then at line 60, is one finding
        before = Fingerprinted(fingerprint(path="a.py", title="t", evidence="+return a*b"), "a.py", 6)
        after = Fingerprinted(before.fingerprint, "a.py", 60)
        self.assertEqual(before.fingerprint, after.fingerprint)
        self.assertEqual(dedupe_by_fingerprint([before, after]), [before])

    def test_re_indentation_does_not_change_the_fingerprint(self) -> None:
        one = fingerprint(path="a.py", title="t", evidence="+    return a * b")
        two = fingerprint(path="a.py", title="t", evidence="+        return a * b")
        self.assertEqual(one, two)

    def test_diff_markers_do_not_change_the_fingerprint(self) -> None:
        one = fingerprint(path="a.py", title="t", evidence="+return a * b")
        two = fingerprint(path="a.py", title="t", evidence="return a * b")
        self.assertEqual(one, two)

    def test_different_quoted_code_is_a_different_finding(self) -> None:
        one = fingerprint(path="a.py", title="t", evidence="+return a * b")
        two = fingerprint(path="a.py", title="t", evidence="+return a / b")
        self.assertNotEqual(one, two)

    def test_a_different_path_is_a_different_finding(self) -> None:
        one = fingerprint(path="a.py", title="t", evidence="+return a * b")
        two = fingerprint(path="b.py", title="t", evidence="+return a * b")
        self.assertNotEqual(one, two)

    def test_a_fixed_then_reintroduced_differently_is_new(self) -> None:
        one = fingerprint(path="a.py", title="t", evidence="+return a * b")
        two = fingerprint(path="a.py", title="t", evidence="+return b * a")
        self.assertNotEqual(one, two)

    def test_titles_that_drift_do_not_change_the_fingerprint(self) -> None:
        # measured on real output: the same finding was titled two different ways across two runs
        one = fingerprint(
            path="calc/__init__.py",
            title="divide() guard changed from b == 0 to b <= 0, so every negative divisor was rejected",
            evidence="-    if b == 0:\n+    if b <= 0:",
        )
        two = fingerprint(
            path="calc/__init__.py",
            title="divide() guard widened from b == 0 to b <= 0, so negative divisors raise",
            evidence="-    if b == 0:\n+    if b <= 0:",
        )
        self.assertEqual(one, two)

    def test_a_finding_with_no_evidence_still_fingerprints(self) -> None:
        one = fingerprint(path="a.py", title="a real problem here", evidence="")
        two = fingerprint(path="a.py", title="a real problem here", evidence="")
        self.assertEqual(one, two)
        self.assertTrue(one)

    @unittest.skipUnless(EVIDENCE_DIR.exists(), "P2 receipts not present")
    def test_two_real_runs_over_identical_code_agree(self) -> None:
        """The measurement that made me deviate from D11's literal wording."""
        first = json.loads((EVIDENCE_DIR / "dryrun-pr18.json").read_text())["findings"][0]
        second = json.loads((EVIDENCE_DIR / "dryrun-pr18-with-intent.json").read_text())["findings"][0]
        self.assertNotEqual(first["title"], second["title"], "the titles really did differ")
        self.assertEqual(
            fingerprint(path=first["path"], title=first["title"], evidence=first["evidence"]),
            fingerprint(path=second["path"], title=second["title"], evidence=second["evidence"]),
        )


class SetOperationTests(unittest.TestCase):
    def test_new_since_reports_only_unseen_fingerprints(self) -> None:
        a = Fingerprinted("aaa", "a.py", 1)
        b = Fingerprinted("bbb", "b.py", 2)
        self.assertEqual(new_since({"aaa"}, [a, b]), [b])

    def test_resolved_since_reports_fingerprints_that_vanished(self) -> None:
        # an important finding from commit 1 that no longer appears has been fixed
        self.assertEqual(resolved_since({"aaa", "bbb"}, [Fingerprinted("aaa", "a.py", 1)]), {"bbb"})

    def test_dedupe_keeps_the_first_and_preserves_order(self) -> None:
        items = [Fingerprinted("a", "x", 1), Fingerprinted("b", "y", 2), Fingerprinted("a", "x", 9)]
        self.assertEqual([i.fingerprint for i in dedupe_by_fingerprint(items)], ["a", "b"])


class NormaliseEvidenceTests(unittest.TestCase):
    def test_whitespace_and_markers_are_stripped(self) -> None:
        self.assertEqual(normalise_evidence("+    return a * b"), "return a * b")

    def test_file_headers_are_kept_out(self) -> None:
        self.assertEqual(normalise_evidence("+++ b/a.py\n+real line"), "real line")

    def test_empty_evidence_is_empty(self) -> None:
        self.assertEqual(normalise_evidence(""), "")


class ReviewStateTests(unittest.TestCase):
    """C7/C8: the state is explicit, including the case with no findings."""

    def test_no_findings_is_stated_not_omitted(self) -> None:
        report = ReviewReport(state=ReviewState.COMPLETE, level="standard", lenses_run=["combined"])
        body = report.render()
        self.assertIn("no findings", body.lower())

    def test_incomplete_is_stated_with_its_reason(self) -> None:
        report = ReviewReport(
            state=ReviewState.INCOMPLETE, level="standard", lenses_run=["combined"],
            errors=["lens combined failed: model unavailable"],
        )
        body = report.render()
        self.assertIn("INCOMPLETE", body)
        self.assertIn("model unavailable", body)

    def test_incremental_is_distinguishable_from_a_full_review(self) -> None:
        report = ReviewReport(state=ReviewState.COMPLETE, level="standard", incremental=True,
                              since_head="abc1234")
        self.assertIn("since abc1234", report.render())

    def test_omitted_pre_existing_is_counted_not_listed(self) -> None:
        report = ReviewReport(state=ReviewState.COMPLETE, level="standard", omitted_pre_existing=3)
        body = report.render()
        self.assertIn("3 pre-existing", body)

    def test_quiet_profile_hides_nits_but_says_so(self) -> None:
        report = ReviewReport(state=ReviewState.COMPLETE, level="quick", profile="quiet",
                              omitted_nits=2)
        body = report.render()
        self.assertIn("2 minor", body)


class VerdictBlockTests(unittest.TestCase):
    """C16: a machine-readable block, so a script can read the verdict without parsing prose."""

    def test_the_block_is_valid_json(self) -> None:
        report = ReviewReport(state=ReviewState.COMPLETE, level="deep")
        block = report.verdict_block()
        parsed = json.loads(block.split("```json", 1)[1].rsplit("```", 1)[0])
        self.assertEqual(parsed["state"], "complete")
        self.assertEqual(parsed["level"], "deep")

    def test_the_block_carries_the_fingerprints(self) -> None:
        report = ReviewReport(
            state=ReviewState.COMPLETE,
            findings=[Fingerprinted("abc123", "a.py", 5)],
        )
        parsed = json.loads(report.verdict_block().split("```json", 1)[1].rsplit("```", 1)[0])
        self.assertEqual(parsed["findings"][0]["fingerprint"], "abc123")

    def test_the_block_never_claims_approval_or_merge(self) -> None:
        report = ReviewReport(state=ReviewState.COMPLETE, level="standard")
        block = report.verdict_block().lower()
        for forbidden in ('"approved"', '"request_changes"', '"merge"', '"merged": true'):
            self.assertNotIn(forbidden, block)


class CacheTests(unittest.TestCase):
    """C20: a review is cached on (repo, head_sha)."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="cs-cache-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))
        self.cache = ReviewCache(self.dir)

    def test_a_miss_then_a_hit(self) -> None:
        self.assertIsNone(self.cache.get("o/r", "abc"))
        self.cache.put("o/r", "abc", {"findings": []})
        self.assertEqual(self.cache.get("o/r", "abc"), {"findings": []})

    def test_a_different_head_is_a_miss(self) -> None:
        self.cache.put("o/r", "abc", {"findings": [1]})
        self.assertIsNone(self.cache.get("o/r", "def"))

    def test_a_different_repo_is_a_miss(self) -> None:
        self.cache.put("o/r", "abc", {"findings": [1]})
        self.assertIsNone(self.cache.get("o/other", "abc"))

    def test_a_corrupt_entry_is_a_miss_not_a_crash(self) -> None:
        self.cache.put("o/r", "abc", {"findings": [1]})
        for path in self.dir.rglob("*.json"):
            path.write_text("{not json")
        self.assertIsNone(self.cache.get("o/r", "abc"))

    def test_the_cache_lives_under_the_state_dir(self) -> None:
        self.cache.put("o/r", "abc", {})
        self.assertTrue(any(self.dir.rglob("abc*.json")))


class WrapUpReserveTests(unittest.TestCase):
    """C2: a run that exhausts its budget still reports incomplete and records it."""

    def test_an_exhausted_budget_produces_an_incomplete_report(self) -> None:
        from code_shipper.budget import BudgetExceeded, RunBudget

        budget = RunBudget(fix_rounds=1, wall_clock_seconds=1)
        budget.start_round()
        with self.assertRaises(BudgetExceeded):
            budget.start_round()
        report = ReviewReport(
            state=ReviewState.INCOMPLETE,
            errors=["budget exhausted: fix_rounds"],
        )
        self.assertIn("INCOMPLETE", report.render())
        self.assertEqual(budget.as_dict()["rounds_used"], 1)


class PathFilterTests(unittest.TestCase):
    """C1: default ignores, and ignored files are reported rather than silently dropped."""

    def test_lockfiles_are_ignored(self) -> None:
        f = PathFilter()
        for path in ("package-lock.json", "poetry.lock", "Cargo.lock", "go.sum", "yarn.lock"):
            with self.subTest(path=path):
                self.assertTrue(f.is_ignored(path))

    def test_generated_and_vendored_paths_are_ignored(self) -> None:
        f = PathFilter()
        for path in ("dist/bundle.js", "node_modules/x/i.js", "vendor/lib.go", "a.min.js"):
            with self.subTest(path=path):
                self.assertTrue(f.is_ignored(path))

    def test_source_and_test_files_are_not_ignored(self) -> None:
        f = PathFilter()
        for path in ("src/app.py", "tests/test_a.py", "docs/guide.md", "calc/__init__.py"):
            with self.subTest(path=path):
                self.assertFalse(f.is_ignored(path))

    def test_extra_ignores_are_additive(self) -> None:
        f = PathFilter(extra_ignores=("docs/**",))
        self.assertTrue(f.is_ignored("docs/a.md"))
        self.assertTrue(f.is_ignored("package-lock.json"), "the defaults still apply")

    def test_include_ignored_is_an_explicit_override(self) -> None:
        # turning the defaults off must be a deliberate act, not a side effect
        self.assertFalse(PathFilter(include_ignored=True).is_ignored("package-lock.json"))

    def test_partition_reports_both_lists_in_order(self) -> None:
        reviewed, ignored = PathFilter().partition(
            ["src/a.py", "package-lock.json", "src/b.py", "dist/x.js"]
        )
        self.assertEqual(reviewed, ["src/a.py", "src/b.py"])
        self.assertEqual(ignored, ["package-lock.json", "dist/x.js"])

    def test_ignored_files_are_summarised_not_silent(self) -> None:
        from code_shipper.review.filters import summarise_skipped

        line = summarise_skipped(["package-lock.json", "dist/a.js"])
        self.assertIn("2 file(s) not reviewed", line)
        self.assertIn("package-lock.json", line)

    def test_no_ignored_files_means_no_line(self) -> None:
        from code_shipper.review.filters import summarise_skipped

        self.assertEqual(summarise_skipped([]), "")

    def test_config_round_trip_and_unknown_keys(self) -> None:
        f = PathFilter.from_config({"extra_ignores": ["docs/**"]})
        self.assertTrue(f.is_ignored("docs/x.md"))
        with self.assertRaises(ValueError):
            PathFilter.from_config({"ignore_all": True})


class RationaleTests(unittest.TestCase):
    """C6: the rationale is present, collapsed, and never crowds out the finding."""

    def test_the_rationale_is_collapsed(self) -> None:
        from code_shipper.review.filters import Rationale

        rendered = Rationale(lens="correctness", raised_by="the operator changed",
                             kept_because="the diff confirms it").render()
        self.assertIn("<details>", rendered)
        self.assertIn("Kept because", rendered)
        self.assertTrue(rendered.rstrip().endswith("</details>"))

    def test_an_empty_rationale_renders_nothing(self) -> None:
        from code_shipper.review.filters import Rationale

        self.assertEqual(Rationale().render(), "")

    def test_the_comment_body_leads_with_the_claim(self) -> None:
        from code_shipper.review.filters import Rationale, comment_body

        body = comment_body(
            title="divide multiplies", body="detail here",
            rationale=Rationale(lens="correctness"),
        )
        self.assertTrue(body.startswith("**divide multiplies**"))
        self.assertIn("detail here", body)
        self.assertIn("<details>", body)

    def test_a_comment_without_a_rationale_is_just_the_finding(self) -> None:
        from code_shipper.review.filters import comment_body

        self.assertNotIn("<details>", comment_body(title="t", body="b"))


class MergeTests(unittest.TestCase):
    """Two different problems quoting the same line must not become one silently.

    Identity is path + evidence, so findings sharing a quoted line share a fingerprint and merge.
    The condition on that: keep the highest severity, name the ones that merged, record the count.
    """

    def _finding(self, severity: str, title: str, path: str = "calc/__init__.py"):
        from code_shipper.review.verify import Finding

        return Finding(
            id=f"f-{severity}",
            path=path,
            line=6,
            severity=severity,
            title=title,
            body=f"detail for {title}",
            evidence="+    return a * b",
        )

    def test_a_correctness_and_a_security_finding_produce_one_posted_finding_naming_both(self) -> None:
        from code_shipper.review.fingerprint import merge_by_fingerprint

        correctness = self._finding("important", "divide multiplies instead of dividing")
        security = self._finding("blocking", "operator swap turns division into multiplication")

        # the two distinct problems share a path and a quoted line
        self.assertEqual(
            fingerprint(path=correctness.path, title=correctness.title, evidence=correctness.evidence),
            fingerprint(path=security.path, title=security.title, evidence=security.evidence),
        )

        merged = merge_by_fingerprint([correctness, security])

        self.assertEqual(len(merged), 1, "one posted finding")
        survivor = merged[0]
        # the more serious one survives
        self.assertEqual(survivor.severity, "blocking")
        self.assertEqual(survivor.title, "operator swap turns division into multiplication")
        # and the other is named, not lost
        self.assertIn("divide multiplies instead of dividing", survivor.body_suffix())
        self.assertIn("Also noted on this line:", survivor.body_suffix())
        self.assertEqual(survivor.merged_count, 1)

    def test_the_merge_keeps_the_highest_severity_whatever_the_order(self) -> None:
        from code_shipper.review.fingerprint import merge_by_fingerprint

        nit = self._finding("nit", "a style nit")
        blocking = self._finding("blocking", "a real bug")
        for order, label in (([nit, blocking], "nit first"), ([blocking, nit], "blocking first")):
            with self.subTest(order=label):
                merged = merge_by_fingerprint(order)
                self.assertEqual(len(merged), 1)
                self.assertEqual(merged[0].severity, "blocking")

    def test_a_single_finding_is_untouched_and_names_nothing(self) -> None:
        from code_shipper.review.fingerprint import merge_by_fingerprint

        merged = merge_by_fingerprint([self._finding("important", "one problem only")])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].merged_count, 0)
        self.assertEqual(merged[0].body_suffix(), "")

    def test_distinct_evidence_does_not_merge(self) -> None:
        from code_shipper.review.fingerprint import merge_by_fingerprint
        from code_shipper.review.verify import Finding

        one = self._finding("important", "problem one")
        two = Finding("f2", "calc/__init__.py", 9, "important", "problem two", "b",
                      "+    return a / b")
        merged = merge_by_fingerprint([one, two])
        self.assertEqual(len(merged), 2, "different code is a different finding")

    def test_the_report_counts_merged_findings(self) -> None:
        report = ReviewReport(state=ReviewState.COMPLETE, level="standard", merged_findings=2)
        self.assertIn("2 additional finding(s) shared a fingerprint", report.render())

    def test_the_verdict_block_records_the_merged_count(self) -> None:
        report = ReviewReport(state=ReviewState.COMPLETE, merged_findings=1)
        parsed = json.loads(report.verdict_block().split("```json", 1)[1].rsplit("```", 1)[0])
        self.assertEqual(parsed["counts"]["merged_away"], 1)

    def test_the_merge_preserves_the_caller_order(self) -> None:
        from code_shipper.review.fingerprint import merge_by_fingerprint
        from code_shipper.review.verify import Finding

        first = self._finding("important", "first")
        second = Finding("f2", "other.py", 2, "important", "second", "b", "+other code")
        merged = merge_by_fingerprint([first, second])
        self.assertEqual([m.title for m in merged], ["first", "second"])


if __name__ == "__main__":
    unittest.main()
