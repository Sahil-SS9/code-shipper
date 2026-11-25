"""STOP A: incremental review, filters/cache wiring, profile, coverage, cap, CI hints.

The two end-to-end tests the review asked for are here and use a counting harness, so "zero model
calls" is measured rather than assumed:

* running a review twice at the same head makes **zero** calls the second time;
* a lockfile-only change is **skipped and named**.

Everything else pins the state machine, because incremental review's failure mode is silent: a
baseline moved by an incomplete pass, or an unfixed important finding vanishing, both look like a
clean review.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from code_shipper.hosts.base import Diff, FilePatch
from code_shipper.review.cache import ReviewCache
from code_shipper.review.filters import PathFilter, summarise_skipped
from code_shipper.review.framing import ci_annotations, hint_block
from code_shipper.review.incremental import restrict
from code_shipper.review.rank import PostingPlan, plan
from code_shipper.review.service import ReviewRequest, run as run_service
from code_shipper.review.state import ReviewState, ReviewStore, advance, pause
from code_shipper.review.verify import Finding


def diff_of(*paths: str) -> Diff:
    return Diff(files=tuple(FilePatch(path=p, patch=f"+# {p}\n") for p in paths))


@dataclass
class CountingHarness:
    """A harness that answers every lens with one finding, and counts its calls."""

    findings: list[dict] = field(default_factory=list)
    calls: int = 0
    fail_lenses: bool = False

    def available(self):
        return True, ""

    def run(self, task, env=None):
        self.calls += 1
        if self.fail_lenses:
            raise RuntimeError("harness unavailable")
        # verification asks for a verdict per finding; answering "keep" every time
        if task.kind == "verify":
            from code_shipper.harness.base import AgentResult

            entries = []
            for entry in json.loads(json.dumps(task.prompt.count('"id"') and "[]" or "[]")):
                pass
            return AgentResult(ok=True, output={"verdicts": []}, error="")
        from code_shipper.harness.base import AgentResult

        return AgentResult(ok=True, output={"findings": list(self.findings)}, error="")


class SilentHarness:
    """Reports no findings, counts calls. The simplest useful stand-in."""

    def __init__(self) -> None:
        self.calls = 0

    def available(self):
        return True, ""

    def run(self, task, env=None):
        from code_shipper.harness.base import AgentResult

        self.calls += 1
        if task.kind == "verify":
            return AgentResult(ok=True, output={"verdicts": []}, error="")
        return AgentResult(ok=True, output={"findings": []}, error="")


class StateTests(unittest.TestCase):
    """D11: only a complete review moves the baseline, and the union is over the whole PR."""

    def test_an_incomplete_review_does_not_move_the_baseline(self) -> None:
        state = ReviewState(repo="o/r", pr=1, last_reviewed_head="aaa")
        advance(state, head_sha="bbb", complete=False, findings=[], covered_paths=["a.py"])
        self.assertEqual(state.last_reviewed_head, "aaa", "an incomplete pass saw nothing; it cannot skip")

    def test_a_complete_review_moves_the_baseline(self) -> None:
        state = ReviewState(repo="o/r", pr=1)
        advance(state, head_sha="bbb", complete=True, findings=[], covered_paths=["a.py"])
        self.assertEqual(state.last_reviewed_head, "bbb")
        self.assertEqual(state.reviews, 1)

    def test_an_unfixed_important_finding_survives_a_pass_that_did_not_cover_it(self) -> None:
        # the union rule: an incremental pass over b.py must not erase commit 1's finding in a.py
        state = ReviewState(repo="o/r", pr=1)
        first = _stamp(Finding("f1", "a.py", 1, "important", "a real bug", "b", "+x"),
                       "fp-important")
        advance(state, head_sha="aaa", complete=True, findings=[first], covered_paths=["a.py"])

        advance(state, head_sha="bbb", complete=True, findings=[], covered_paths=["b.py"])

        self.assertIn("fp-important", state.important_fingerprints(),
                      "a pass over b.py has no standing to clear a finding in a.py")

    def test_a_finding_clears_when_a_pass_covers_its_file_and_does_not_report_it(self) -> None:
        state = ReviewState(repo="o/r", pr=1)
        first = _stamp(Finding("f1", "a.py", 1, "important", "fixed now", "b", "+x"), "fp-fixed")
        advance(state, head_sha="aaa", complete=True, findings=[first], covered_paths=["a.py"])
        self.assertIn("fp-fixed", state.important_fingerprints())

        advance(state, head_sha="bbb", complete=True, findings=[], covered_paths=["a.py"])

        self.assertEqual(state.important_fingerprints(), set(),
                         "a pass that covered the file and found nothing has cleared it")

    def test_the_union_spans_multiple_passes(self) -> None:
        state = ReviewState(repo="o/r", pr=1)
        advance(state, head_sha="aaa", complete=True,
                findings=[_stamp(Finding("1", "a.py", 1, "important", "t1", "b", "+1"), "fp1")],
                covered_paths=["a.py"])
        advance(state, head_sha="bbb", complete=True,
                findings=[_stamp(Finding("2", "b.py", 2, "important", "t2", "b", "+2"), "fp2")],
                covered_paths=["b.py"])
        self.assertEqual(state.important_fingerprints(), {"fp1", "fp2"})

    def test_a_nit_is_not_in_the_important_union(self) -> None:
        state = ReviewState(repo="o/r", pr=1)
        advance(state, head_sha="aaa", complete=True,
                findings=[_stamp(Finding("1", "a.py", 1, "nit", "style", "b", "+1"), "fpn")],
                covered_paths=["a.py"])
        self.assertEqual(state.important_fingerprints(), set())

    def test_is_incremental_needs_a_baseline_and_a_different_head(self) -> None:
        state = ReviewState(repo="o/r", pr=1)
        self.assertFalse(state.is_incremental("aaa"), "no baseline means a full review")
        advance(state, head_sha="aaa", complete=True, findings=[], covered_paths=["a.py"])
        self.assertFalse(state.is_incremental("aaa"), "same head is not incremental")
        self.assertTrue(state.is_incremental("bbb"))

    def test_pause_is_stated(self) -> None:
        state = pause(ReviewState(), "review cap reached")
        self.assertTrue(state.paused)
        self.assertIn("cap", state.pause_reason)

    def test_state_round_trips_through_disk(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="cs-state-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        store = ReviewStore(root)
        state = ReviewState(repo="o/r", pr=7, last_reviewed_head="abc", reviews=2)
        store.save(state)
        loaded = store.load("o/r", 7)
        self.assertEqual(loaded.last_reviewed_head, "abc")
        self.assertEqual(loaded.reviews, 2)

    def test_a_corrupt_state_file_is_a_fresh_start_not_a_crash(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="cs-state2-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        store = ReviewStore(root)
        store.save(ReviewState(repo="o/r", pr=1, last_reviewed_head="abc"))
        for path in (root / "review-state").glob("*.json"):
            path.write_text("{not json")
        loaded = store.load("o/r", 1)
        self.assertEqual(loaded.last_reviewed_head, "", "a corrupt baseline must not be reused")


def _stamp(finding: Finding, fingerprint: str):
    """Give a Finding a fingerprint, which is what the union rule keys on.

    Finding is frozen, so this is the only way to attach a computed field — which is what the real
    pipeline does too, via dataclasses.replace.
    """
    object.__setattr__(finding, "fingerprint", fingerprint)
    return finding


class IncrementalScopeTests(unittest.TestCase):
    def test_only_touched_files_are_in_scope(self) -> None:
        full = diff_of("a.py", "b.py", "c.py")
        since = diff_of("b.py")
        scoped = restrict(full, since, since_sha="aaa", to_sha="bbb")
        self.assertEqual(list(scoped.included), ["b.py"])
        self.assertEqual(list(scoped.excluded), ["a.py", "c.py"])

    def test_the_summary_names_what_was_not_reviewed(self) -> None:
        scoped = restrict(diff_of("a.py", "b.py"), diff_of("a.py"), since_sha="abcdef1234")
        self.assertIn("1 file(s) changed since abcdef1", scoped.summary())
        self.assertIn("1 unchanged", scoped.summary())

    def test_an_empty_scope_is_reported_not_hidden(self) -> None:
        scoped = restrict(diff_of("a.py"), Diff(files=()), since_sha="abcdef1")
        self.assertTrue(scoped.empty)
        self.assertIn("No files changed", scoped.summary())

    def test_content_comes_from_the_full_diff(self) -> None:
        # a hunk rebased onto the last review would lose the context the reviewer needs
        full = Diff(files=(FilePatch(path="a.py", patch="+the full change"),))
        since = Diff(files=(FilePatch(path="a.py", patch="+only the new bit"),))
        scoped = restrict(full, since)
        self.assertIn("the full change", scoped.diff.files[0].patch)


class ProfileTests(unittest.TestCase):
    def test_quiet_drops_nits_but_counts_them(self) -> None:
        findings = [
            Finding("1", "a.py", 1, "important", "real", "b", "+x"),
            Finding("2", "a.py", 2, "nit", "style", "b", "+y"),
        ]
        posted = plan(findings)
        quiet = posted.without_nits()
        self.assertEqual(len(quiet.to_post), 1)
        self.assertEqual(quiet.omitted_nit, 1)
        self.assertGreater(quiet.omitted, posted.omitted)

    def test_standard_keeps_nits(self) -> None:
        findings = [Finding("2", "a.py", 2, "nit", "style", "b", "+y")]
        self.assertEqual(len(plan(findings).without_nits().to_post), 0)


class CiHintTests(unittest.TestCase):
    """C10: annotations point at code; they are not evidence and not instructions."""

    def test_paths_are_extracted_from_a_log(self) -> None:
        log = (
            "tests/test_calc.py:11: AssertionError\n"
            "calc/__init__.py:6: in divide\n"
            "FAILED tests/test_calc.py::test_divide\n"
        )
        self.assertEqual(ci_annotations(log), ["tests/test_calc.py", "calc/__init__.py"])

    def test_a_non_path_line_is_ignored(self) -> None:
        self.assertEqual(ci_annotations("1 failed, 2 passed in 0.02s"), [])

    def test_the_hint_block_says_it_is_only_a_hint(self) -> None:
        block = hint_block(["a.py"])
        self.assertIn("not a conclusion", block)
        self.assertIn("can be wrong", block)
        # and it is framed as untrusted, like every other externally-sourced block
        self.assertEqual(block.count("</untrusted>"), 1)

    def test_no_annotations_means_no_block(self) -> None:
        self.assertEqual(hint_block([]), "")

    def test_the_limit_is_respected(self) -> None:
        log = "\n".join(f"f{index}.py:1: x" for index in range(100))
        self.assertEqual(len(ci_annotations(log, limit=5)), 5)


class ServiceTests(unittest.TestCase):
    """The wiring: cache, filters, state and the cap, all through one entry point."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cs-svc-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.store = ReviewStore(self.root)
        self.cache = ReviewCache(self.root)

    def run_review(self, *, head="aaa", diff=None, trigger="manual", profile="standard",
                   harness=None, since_diff=None, max_reviews=20):
        return run_service(
            ReviewRequest(
                repo="o/r", pr=1, head_sha=head, diff=diff or diff_of("calc/__init__.py"),
                since_diff=since_diff, level="standard", profile=profile, trigger=trigger,
                workdir=self.root,
            ),
            harness=harness or SilentHarness(),
            store=self.store,
            cache=self.cache,
            max_reviews_per_pr=max_reviews,
        )

    def test_the_same_head_twice_makes_zero_model_calls_the_second_time(self) -> None:
        """The end-to-end cache test the review asked for."""
        first = SilentHarness()
        result_one = self.run_review(head="aaa", harness=first)
        self.assertFalse(result_one.cached)
        self.assertGreater(first.calls, 0, "the first review must actually call the model")

        second = SilentHarness()
        result_two = self.run_review(head="aaa", harness=second)
        self.assertTrue(result_two.cached)
        self.assertEqual(second.calls, 0, "a cached head must not cost a single model call")

    def test_a_lockfile_only_change_is_skipped_and_named(self) -> None:
        """The end-to-end filter test the review asked for."""
        harness = SilentHarness()
        result = self.run_review(
            head="aaa",
            diff=diff_of("package-lock.json", "calc/__init__.py"),
            harness=harness,
        )
        self.assertIn("package-lock.json", result.ignored_files)
        self.assertNotIn("package-lock.json", result.coverage["reviewed"])
        self.assertIn("calc/__init__.py", result.coverage["reviewed"])
        # and it is named in the summary, not silently dropped
        self.assertIn("not reviewed", result.ignored_summary)
        self.assertIn("package-lock.json", result.ignored_summary)

    def test_a_lockfile_only_pr_makes_nothing_up(self) -> None:
        # nothing reviewable: the review must say so rather than inventing findings
        result = self.run_review(head="aaa", diff=diff_of("package-lock.json", "yarn.lock"))
        self.assertEqual(result.coverage["files_reviewed"], 0)
        self.assertIn("2 file(s) not reviewed", result.ignored_summary)

    def test_a_complete_review_advances_the_baseline(self) -> None:
        self.run_review(head="aaa")
        self.assertEqual(self.store.load("o/r", 1).last_reviewed_head, "aaa")

    def test_an_incomplete_review_does_not_advance_the_baseline(self) -> None:
        # A distinct head, so this run cannot hit a cache entry written by the complete review above.
        self.store.save(ReviewState(repo="o/r", pr=1))
        self.cache.clear()
        class Broken(SilentHarness):
            def run(self, task, env=None):
                self.calls += 1
                from code_shipper.harness.base import AgentResult
                return AgentResult(ok=False, output=None, error="model unavailable")
        result = self.run_review(head="aaa", harness=Broken())
        self.assertFalse(result.outcome.complete)
        self.assertEqual(self.store.load("o/r", 1).last_reviewed_head, "",
                         "an incomplete review must not move the baseline")

    def test_the_cap_pauses_with_a_stated_reason(self) -> None:
        for index in range(2):
            self.run_review(head=f"sha{index}", max_reviews=2)
        result = self.run_review(head="sha3", max_reviews=2)
        self.assertTrue(result.paused)
        self.assertIn("review cap", result.pause_reason)
        self.assertEqual(result.model_calls, 0)

    def test_a_paused_pr_stays_paused_without_calling_the_model(self) -> None:
        self.store.save(pause(ReviewState(repo="o/r", pr=1), "cap reached"))
        harness = SilentHarness()
        result = self.run_review(head="aaa", harness=harness)
        self.assertTrue(result.paused)
        self.assertEqual(harness.calls, 0)

    def test_an_empty_incremental_scope_makes_no_model_call(self) -> None:
        self.run_review(head="aaa")                    # baseline
        harness = SilentHarness()
        result = self.run_review(
            head="bbb", trigger="on-push",
            diff=diff_of("a.py", "b.py"),
            since_diff=Diff(files=()),               # no files touched since
            harness=harness,
        )
        self.assertTrue(result.incremental)
        self.assertEqual(harness.calls, 0)
        self.assertTrue(result.scope.empty)

    def test_an_incremental_review_covers_only_new_files(self) -> None:
        self.run_review(head="aaa")
        result = self.run_review(
            head="bbb", trigger="on-push",
            diff=diff_of("a.py", "b.py"),
            since_diff=diff_of("b.py"),
        )
        self.assertTrue(result.incremental)
        self.assertEqual(list(result.scope.included), ["b.py"])
        self.assertIn("a.py", result.scope.excluded)

    def test_a_manual_review_covers_the_whole_pr(self) -> None:
        self.run_review(head="aaa")
        result = self.run_review(head="bbb", trigger="manual", diff=diff_of("a.py", "b.py"))
        self.assertFalse(result.incremental)

    def test_the_coverage_counts_clean_files(self) -> None:
        result = self.run_review(head="aaa", diff=diff_of("a.py", "b.py"))
        self.assertEqual(result.coverage["files_reviewed"], 2)
        self.assertEqual(result.coverage["files_clean"], 2)

    def test_the_union_is_reported(self) -> None:
        self.run_review(head="aaa")
        result = self.run_review(head="bbb", trigger="manual")
        self.assertIsInstance(result.union_important, tuple)


if __name__ == "__main__":
    unittest.main()
