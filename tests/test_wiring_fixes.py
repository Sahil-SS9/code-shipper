"""The five wiring defects a real run found, each with the test that proves the fix.

Written after the fact as one file because they are one round of feedback, but each test stands alone and
each was checked to fail against the old behaviour where that was possible.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from code_shipper.contain.spec import credential_location
from code_shipper.review.fingerprint import (
    apply_root_cause_merge,
    enclosing_function,
    merge_by_root_cause,
)
from code_shipper.review.state import ReviewState, ReviewStore
from code_shipper.review.verify import Finding
from code_shipper.surfaces import cli_view, desktop, loader, tui


def _store(root: Path, *, pr: int = 25, title: str = "Add clamp() helper",
           findings: list[dict] | None = None, complete: bool = True) -> None:
    ReviewStore(root).save(ReviewState(
        repo="Sahil-SS9/code-shipper-sandbox", pr=pr,
        last_reviewed_head="9c2eca0a" if complete else "",
        title=title,
        open_all={f["path"]: f for f in (findings or [])},
        open_important={f["path"]: f for f in (findings or [])},
        reviews=1, last_review_at=1767225600.0,
    ))


class TitleTests(unittest.TestCase):
    """Fix 1: the PR title is stored at review time and shown on every surface."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cs-title-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        _store(self.root)

    def test_the_state_carries_the_title(self) -> None:
        state = ReviewStore(self.root).load("Sahil-SS9/code-shipper-sandbox", 25)
        self.assertEqual(state.title, "Add clamp() helper")

    def test_the_title_survives_a_round_trip(self) -> None:
        loaded = ReviewStore(self.root).load("Sahil-SS9/code-shipper-sandbox", 25)
        self.assertEqual(loaded.as_dict()["title"], "Add clamp() helper")

    def test_the_status_row_shows_the_title(self) -> None:
        projection = loader.load_projection("Sahil-SS9/code-shipper-sandbox", 25,
                                            state_dir=self.root)
        lines = cli_view.render_status([projection])
        text = "\n".join(lines)
        self.assertIn("Add clamp() helper", text)

    def test_the_tui_shows_the_title(self) -> None:
        projection = loader.load_projection("Sahil-SS9/code-shipper-sandbox", 25,
                                            state_dir=self.root)
        self.assertIn("Add clamp() helper", "\n".join(tui.render(projection, width=124)))

    def test_the_desktop_shows_the_title(self) -> None:
        projection = loader.load_projection("Sahil-SS9/code-shipper-sandbox", 25,
                                            state_dir=self.root)
        self.assertIn("Add clamp() helper", desktop.render(projection))

    def test_an_older_state_without_a_title_still_renders(self) -> None:
        """State written by the previous version has no title field; it must not crash a board."""
        root = Path(tempfile.mkdtemp(prefix="cs-old-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        path = root / "review-state" / "x.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"repo": "X/Y", "pr": 1, "last_reviewed_head": "abc"}))
        projection = loader.load_projection("X/Y", 1, state_dir=root)
        self.assertEqual(projection.title, "")
        cli_view.render_status([projection])  # must not raise


class FooterAndEmptyStateTests(unittest.TestCase):
    """Fix 2: the footer names a real pull request or nothing; one empty message."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cs-footer-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def test_the_footer_does_not_hardcode_a_pull_request(self) -> None:
        _store(self.root, pr=42)
        projection = loader.load_projection("Sahil-SS9/code-shipper-sandbox", 42,
                                            state_dir=self.root)
        text = "\n".join(cli_view.render_status([projection]))
        self.assertNotIn("#25", text)
        self.assertIn("--pr 42", text)

    def test_the_empty_state_prints_one_message(self) -> None:
        lines = cli_view.render_status([])
        text = "\n".join(lines)
        self.assertEqual(text.count("No pull requests have been reviewed yet"), 1,
                         f"the empty state repeats itself:\n{text}")

    def test_the_empty_state_still_says_what_to_do(self) -> None:
        text = "\n".join(cli_view.render_status([]))
        self.assertIn("code-shipper review", text)

    def test_the_empty_state_has_no_footer_advice(self) -> None:
        text = "\n".join(cli_view.render_status([]))
        self.assertNotIn("Open one:", text)

    def test_the_cli_does_not_add_a_second_empty_message(self) -> None:
        from code_shipper import cli

        parser = cli.build_parser()
        source = Path(cli.__file__).read_text()
        # Guard the specific regression: the handler used to print its own copy after the renderer.
        self.assertNotIn('print("  Or see the sample board', source)


class WatchedCountTests(unittest.TestCase):
    """Fix 3: a reviewed pull request is reviewed, not watched."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cs-watch-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        _store(self.root)

    def _text(self, watched: int = 0) -> str:
        projection = loader.load_projection("Sahil-SS9/code-shipper-sandbox", 25,
                                            state_dir=self.root)
        return "\n".join(cli_view.render_status([projection], watched=watched))

    def test_a_one_off_review_is_not_described_as_watched(self) -> None:
        text = self._text()
        self.assertNotIn("watching 1", text)
        self.assertIn("not watching", text)

    def test_it_does_not_claim_an_interval_nothing_honours(self) -> None:
        self.assertNotIn("checked every", self._text())

    def test_it_still_says_how_many_were_reviewed(self) -> None:
        self.assertIn("1 reviewed", self._text())

    def test_a_real_watch_is_counted_when_one_exists(self) -> None:
        text = self._text(watched=1)
        self.assertIn("watching 1", text)
        self.assertIn("checked every", text)

    def test_the_header_never_claims_a_review_is_a_watch_via_the_cli(self) -> None:
        from code_shipper import cli

        source = Path(cli.__file__).read_text()
        self.assertIn("watched = 0", source,
                      "the CLI must not count reviewed pull requests as watched")


class DuplicateFindingTests(unittest.TestCase):
    """Fix 4: the same root cause at two lines is one finding, named twice."""

    def _pair(self, *, path: str = "scn05_clamp.py", first_line: int = 2,
              second_line: int = 4, titles: tuple[str, str] | None = None
              ) -> list[Finding]:
        first_title, second_title = titles or (
            "clamp() never applies the upper bound",
            "clamp() never applies the upper bound",
        )
        return [
            Finding(id="a", path=path, line=first_line, severity="important",
                    title=first_title, body="", 
                    evidence="def clamp(x, low, high):\n    return max(low, x)"),
            Finding(id="b", path=path, line=second_line, severity="important",
                    title=second_title, body="",
                    evidence="def clamp(x, low, high):\n    return max(low, min(x))"),
        ]

    def test_the_case_that_was_reported_merges(self) -> None:
        """The exact defect: :2 and :4, same function, same claim, counted as two."""
        merged = apply_root_cause_merge(self._pair())
        self.assertEqual(len(merged), 1, "the same bug at two lines must count once")

    def test_the_merged_finding_names_the_other_line(self) -> None:
        merged = apply_root_cause_merge(self._pair())
        self.assertIn("Also noted", merged[0].body)
        self.assertIn("line 4", merged[0].body)

    def test_merging_keeps_the_higher_severity(self) -> None:
        items = self._pair()
        items[1] = Finding(id="b", path=items[1].path, line=4, severity="blocking",
                           title=items[1].title, body="", evidence=items[1].evidence)
        merged = apply_root_cause_merge(items)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].severity, "blocking")

    def test_different_functions_do_not_merge(self) -> None:
        items = [
            Finding(id="a", path="m.py", line=1, severity="important", title="x is missing",
                    body="", evidence="def alpha():\n  pass"),
            Finding(id="b", path="m.py", line=9, severity="important", title="x is missing",
                    body="", evidence="def beta():\n  pass"),
        ]
        self.assertEqual(len(apply_root_cause_merge(items)), 2,
                         "different functions are different root causes")

    def test_different_claims_in_one_function_do_not_merge(self) -> None:
        items = self._pair(titles=("clamp() never applies the upper bound",
                                   "clamp() raises on a reversed range"))
        self.assertEqual(len(apply_root_cause_merge(items)), 2)

    def test_no_enclosing_function_does_not_merge(self) -> None:
        """Top-level code: "somewhere in this file" is not a root cause."""
        items = [
            Finding(id="a", path="m.py", line=1, severity="important", title="x missing",
                    body="", evidence="x = 1"),
            Finding(id="b", path="m.py", line=2, severity="important", title="x missing",
                    body="", evidence="y = 2"),
        ]
        self.assertEqual(len(apply_root_cause_merge(items)), 2)

    def test_a_single_finding_is_returned_untouched(self) -> None:
        one = self._pair()[:1]
        self.assertIs(apply_root_cause_merge(one), one)

    def test_nothing_merged_returns_the_original_list_object(self) -> None:
        items = self._pair(titles=("clamp() never applies the upper bound",
                                   "clamp() raises on a reversed range"))
        self.assertIs(apply_root_cause_merge(items), items,
                      "a caller must be able to tell that nothing merged")

    def test_enclosing_function_reads_the_last_def(self) -> None:
        self.assertEqual(enclosing_function("def outer():\n  def inner():\n    pass"), "inner")
        self.assertEqual(enclosing_function("x = 1"), "")
        self.assertEqual(enclosing_function(""), "")

    def test_the_existing_fingerprint_merge_still_works(self) -> None:
        """The new rule must not replace the old one for identical evidence."""
        items = [
            Finding(id="a", path="m.py", line=1, severity="important", title="t",
                    body="", evidence="def f():\n  return 1"),
            Finding(id="b", path="m.py", line=1, severity="nit", title="t",
                    body="", evidence="def f():\n  return 1"),
        ]
        self.assertEqual(len(apply_root_cause_merge(items)), 1)


class CredentialGuardTests(unittest.TestCase):
    """Fix 5: refuse credential locations, not any path under a dot directory."""

    def test_the_live_hermes_home_is_refused(self) -> None:
        self.assertTrue(credential_location("/home/kensei/.hermes"))

    def test_hermes_auth_is_refused(self) -> None:
        for path in ("/home/kensei/.hermes/auth.json", "/home/kensei/.hermes/auth/token",
                     "/home/kensei/.hermes/tokens/github", "/home/kensei/.hermes/secrets/x"):
            self.assertTrue(credential_location(path), path)

    def test_a_checkout_under_hermes_is_allowed(self) -> None:
        """The defect: a clone at ~/.hermes/repos/x could not run a review at all."""
        for path in ("/home/kensei/.hermes/repos/code-shipper",
                     "/home/kensei/.hermes/cache/scratch/x",
                     "/home/kensei/.hermes/logs"):
            self.assertEqual(credential_location(path), "", path)

    def test_ssh_and_cloud_credentials_are_refused(self) -> None:
        for path in ("/home/kensei/.ssh", "/home/kensei/.ssh/id_rsa",
                     "/home/kensei/.aws/credentials", "/home/kensei/.gnupg/secring.gpg",
                     "/home/kensei/.docker/config.json", "/home/kensei/.kube/config"):
            self.assertTrue(credential_location(path), path)

    def test_an_ordinary_checkout_is_allowed(self) -> None:
        for path in ("/home/kensei/repos/code-shipper", "/workspace/src", "/tmp/run-1/work"):
            self.assertEqual(credential_location(path), "", path)

    def test_a_dot_config_dir_is_allowed(self) -> None:
        """`/.config` was refused before, which is an ordinary directory, not a credential store."""
        self.assertEqual(credential_location("/home/kensei/.config/some-app"), "")

    def test_windows_paths_are_matched(self) -> None:
        self.assertTrue(credential_location("C:\\Users\\x\\.hermes"))
        self.assertTrue(credential_location("C:\\Users\\x\\.ssh\\id_rsa"))
        self.assertEqual(credential_location("C:\\Users\\x\\.hermes\\repos\\cs"), "")

    def test_a_lookalike_directory_is_not_refused(self) -> None:
        """`.sshfoo` is not `.ssh`; matching components rather than substrings is the point."""
        self.assertEqual(credential_location("/home/kensei/.sshfoo/key"), "")

    def test_the_refusal_names_the_path_and_the_fix(self) -> None:
        from code_shipper.contain.spec import ContainerSpec

        with self.assertRaises(ValueError) as caught:
            ContainerSpec(
                image="img", source_dir=Path("/home/kensei/.ssh/repo"),
                work_dir=Path("/tmp/w"), broker_dir=Path("/tmp/broker"),
                entrypoint="/bin/true",
            ).validate()
        message = str(caught.exception)
        self.assertIn("/home/kensei/.ssh/repo", message)
        self.assertIn("~/repos", message, "the error must say what to do")


if __name__ == "__main__":
    unittest.main()
