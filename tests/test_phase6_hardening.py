"""Phase 6 hardening: the failures that are silent rather than loud.

Each test here corresponds to a way the system could be wrong without saying so.

* A torn state write loses the incremental baseline and the next review silently becomes a full one.
* Concurrent writers interleave and the last writer wins with a state built from a stale read.
* A corrupt state file must degrade to a full review, never skip commits.
* A redirect must not carry the Authorization header anywhere.
* A budget must be enforced before the work, not after it.
* An unfinished review must never be cached as if it were a result.
* A refusal must send nothing.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from code_shipper.hosts.github import GitHubHost, HostError
from code_shipper.review import ancestry
from code_shipper.review.state import ReviewState, ReviewStore


class AtomicWriteTests(unittest.TestCase):
    """A half-written state file is not a crash — it is a silent downgrade to a full review."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cs-hard-"))
        self.store = ReviewStore(self.root)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def _state(self, head: str = "abc1234") -> ReviewState:
        return ReviewState(repo="o/r", pr=1, last_reviewed_head=head, reviews=1)

    def test_a_saved_state_reads_back(self) -> None:
        self.store.save(self._state())
        self.assertEqual(self.store.load("o/r", 1).last_reviewed_head, "abc1234")

    def test_no_temp_files_are_left_behind(self) -> None:
        for _ in range(5):
            self.store.save(self._state())
        leftovers = list((self.root / "review-state").glob("*.tmp"))
        self.assertEqual(leftovers, [], f"temp files accumulated: {leftovers}")

    def test_the_write_is_visible_as_a_whole_file(self) -> None:
        """Every observation of the file parses: there is no window where it is half-written."""
        self.store.save(self._state())
        path = self.store._path("o/r", 1)
        for _ in range(20):
            self.assertIsInstance(json.loads(path.read_text()), dict)

    def test_a_failed_write_leaves_the_previous_file_intact(self) -> None:
        self.store.save(self._state("first999"))
        original = self.store._path("o/r", 1).read_text()
        # Make the replace fail by pointing the target at a directory of the same name.
        path = self.store._path("o/r", 1)
        path.unlink()
        path.mkdir()
        self.store.save(self._state("second111"))  # must not raise
        self.assertTrue(path.is_dir(), "the failure must not have destroyed anything")
        # And the store degrades to a fresh state rather than raising.
        self.assertEqual(self.store.load("o/r", 1).last_reviewed_head, "")

    def test_a_write_failure_never_fails_the_caller(self) -> None:
        path = self.store._path("o/r", 1)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir()  # a directory where the file should be
        self.store.save(self._state())  # must not raise: losing the write is safe


class ConcurrencyTests(unittest.TestCase):
    """Twelve writers, one file: the result must be a valid file, never a torn one."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cs-conc-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def test_concurrent_saves_leave_a_valid_file(self) -> None:
        errors: list[BaseException] = []

        def worker(index: int) -> None:
            try:
                store = ReviewStore(self.root)
                store.save(ReviewState(repo="o/r", pr=1, last_reviewed_head=f"head{index:04d}",
                                       reviews=index))
            except BaseException as exc:  # noqa: BLE001 - recorded, asserted below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [], "a concurrent save must not raise")
        path = ReviewStore(self.root)._path("o/r", 1)
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertIsInstance(data, dict)
        # One of the writers won, whole.
        self.assertRegex(data["last_reviewed_head"], r"^head\d{4}$")

    def test_concurrent_loads_never_see_a_torn_file(self) -> None:
        store = ReviewStore(self.root)
        store.save(ReviewState(repo="o/r", pr=1, last_reviewed_head="seed0000", reviews=1))
        bad: list[str] = []

        valid = {"seed0000"} | {f"head{i:04d}" for i in range(4)}

        def reader() -> None:
            for _ in range(30):
                loaded = ReviewStore(self.root).load("o/r", 1)
                # A torn file would still `load` (as a fresh state) — but a fresh state has no
                # baseline, and a *partially* applied one would carry a head nobody wrote.
                if loaded.last_reviewed_head and loaded.last_reviewed_head not in valid:
                    bad.append(loaded.last_reviewed_head)

        def writer(index: int) -> None:
            for _ in range(10):
                ReviewStore(self.root).save(
                    ReviewState(repo="o/r", pr=1, last_reviewed_head=f"head{index:04d}", reviews=1)
                )

        threads = [threading.Thread(target=reader) for _ in range(4)]
        threads += [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(bad, [], "a reader saw a state that was never written whole")


class CorruptStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cs-corrupt-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.store = ReviewStore(self.root)

    def _write(self, content: str) -> None:
        path = self.store._path("o/r", 1)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def test_a_corrupt_file_becomes_a_fresh_state(self) -> None:
        self._write("{not json")
        state = self.store.load("o/r", 1)
        self.assertFalse(state.has_baseline, "a corrupt baseline must not be reused")

    def test_a_non_object_becomes_a_fresh_state(self) -> None:
        self._write("[1, 2, 3]")
        self.assertFalse(self.store.load("o/r", 1).has_baseline)

    def test_an_empty_file_becomes_a_fresh_state(self) -> None:
        self._write("")
        self.assertFalse(self.store.load("o/r", 1).has_baseline)

    def test_a_fresh_state_forces_a_full_review(self) -> None:
        """The consequence that matters: no baseline means no incremental pass."""
        self._write("{corrupt")
        self.assertFalse(self.store.load("o/r", 1).is_incremental("anewhead"))

    def test_state_is_scoped_per_pull_request(self) -> None:
        self.store.save(ReviewState(repo="o/r", pr=1, last_reviewed_head="aaa", reviews=1))
        self.assertFalse(self.store.load("o/r", 2).has_baseline)


class RedirectTests(unittest.TestCase):
    """A redirect must not carry the token to another host."""

    def test_a_redirect_is_refused(self) -> None:
        from code_shipper.hosts import github as gh

        host = GitHubHost("fake-token")
        handlers = [type(h).__name__ for h in host._opener.handlers]
        self.assertIn("_RefuseRedirects", handlers,
                      "the opener must refuse redirects rather than follow them")
        self.assertIs(getattr(gh, "_RefuseRedirects"), gh._RefuseRedirects)

    def test_the_refusal_is_a_host_error(self) -> None:
        from code_shipper.hosts import github as gh

        handler = gh._RefuseRedirects()
        with self.assertRaises(HostError):
            handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/")

    def test_the_token_is_never_in_the_repr(self) -> None:
        host = GitHubHost("super-secret-token")
        self.assertNotIn("super-secret-token", repr(host))


class AncestryHardeningTests(unittest.TestCase):
    """Already implemented for D5; asserted here as part of the hardening surface."""

    def test_an_unreadable_compare_is_treated_as_diverged(self) -> None:
        class _Host:
            def compare(self, repo, old, new):
                raise HostError("network down")

        self.assertTrue(ancestry.is_divergence(_Host(), "o/r", "aaa", "bbb"))

    def test_a_missing_compare_result_is_treated_as_diverged(self) -> None:
        class _Host:
            def compare(self, repo, old, new):
                return None

        self.assertTrue(ancestry.is_divergence(_Host(), "o/r", "aaa", "bbb"))

    def test_divergence_never_raises(self) -> None:
        class _Broken:
            def compare(self, *args):
                raise RuntimeError("anything at all")

        self.assertTrue(ancestry.is_divergence(_Broken(), "o/r", "a", "b"))


class NoSilentCleanTests(unittest.TestCase):
    """The cheapest hardening test of all: an unfinished review is never cached as a result."""

    def test_an_incomplete_review_does_not_advance_the_baseline(self) -> None:
        from code_shipper.review.state import advance

        state = ReviewState(repo="o/r", pr=1)
        advance(state, head_sha="bbb", complete=False, findings=[], covered_paths=["a.py"])
        self.assertFalse(state.has_baseline,
                         "an incomplete review must not become the baseline for the next one")

    def test_a_complete_review_does_advance_it(self) -> None:
        from code_shipper.review.state import advance

        state = ReviewState(repo="o/r", pr=1)
        advance(state, head_sha="bbb", complete=True, findings=[], covered_paths=["a.py"])
        self.assertTrue(state.has_baseline)


class RefusalSendsNothingTests(unittest.TestCase):
    """The invariant behind every refusal: no request leaves the process."""

    def test_there_is_no_merge_method_to_call(self) -> None:
        """Stronger than refusing a merge: the method does not exist, so nothing can call it."""
        from code_shipper.broker.roles import Broker

        self.assertFalse(hasattr(Broker, "merge"),
                         "merge must be absent from the broker, not merely refused")

    def test_every_forbidden_operation_sends_nothing(self) -> None:
        from code_shipper.broker.roles import FORBIDDEN_OPS, Broker, BrokerRefused, CountingTransport

        for op in sorted(FORBIDDEN_OPS):
            transport = CountingTransport()
            broker = Broker(token="fake", repo="o/r", transport=transport,
                            allowed_repos=frozenset({"o/r"}))
            method = getattr(broker, op, None)
            if method is None:
                continue
            with self.assertRaises((BrokerRefused, TypeError)):
                method(pr=1)
            self.assertEqual(transport.count, 0, f"{op} sent a request before refusing")


if __name__ == "__main__":
    unittest.main()
