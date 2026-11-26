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


class AlsoNotedTests(unittest.TestCase):
    """The #25 output shape: "Also noted on this line: PR adds an undocumented w".

    Two defects in one line — the finding's own title was listed under itself, and the joined string was
    cut mid-word by the surface. Both are fixed at their source.
    """

    TITLE = "PR adds an undocumented wrapper around clamp()"

    def _pair(self, *, first: int = 2, second: int = 4, title: str | None = None,
              second_title: str | None = None) -> list[Finding]:
        return [
            Finding(id="a", path="scn05_clamp.py", line=first, severity="important",
                    title=title or self.TITLE, body="",
                    evidence="def clamp(x, low, high):\n    return max(low, x)"),
            Finding(id="b", path="scn05_clamp.py", line=second, severity="important",
                    title=second_title or title or self.TITLE, body="",
                    evidence="def clamp(x, low, high):\n    return max(low, min(x))"),
        ]

    def test_the_finding_never_lists_its_own_title(self) -> None:
        merged = apply_root_cause_merge(self._pair())
        self.assertEqual(len(merged), 1)
        suffix = merged[0].body
        # The survivor's own title must not appear a second time inside its body.
        self.assertNotIn(self.TITLE, suffix,
                         f"the finding restated its own title: {suffix!r}")

    def test_it_names_the_other_line_when_that_is_the_only_difference(self) -> None:
        suffix = apply_root_cause_merge(self._pair())[0].body
        self.assertIn("line 4", suffix)

    def test_it_says_nothing_false_when_the_line_matches(self) -> None:
        """Same title, same line: there is nothing worth adding, and it must not invent a line."""
        items = self._pair(first=2, second=2)
        items[1] = Finding(id="b", path="scn05_clamp.py", line=2, severity="important",
                           title=self.TITLE, body="", evidence="def clamp(x, low, h):\n  return 1")
        merged = apply_root_cause_merge(items)
        if merged[0].body:
            self.assertNotIn("line 2", merged[0].body)

    def test_no_title_is_cut_mid_word(self) -> None:
        """The exact defect: "PR adds an undocumented w"."""
        suffix = apply_root_cause_merge(self._pair())[0].body
        self.assertNotIn("undocumented w\u2026", suffix)
        self.assertNotIn("undocumented w;", suffix)

    def test_clipping_is_word_safe_in_the_tui(self) -> None:
        from code_shipper.surfaces.tui import _clip

        for width in range(8, 60):
            clipped = _clip(f"also noted: {self.TITLE}", width)
            self.assertLessEqual(len(clipped), max(width, 1))
            if clipped.endswith("…"):
                body = clipped[:-1].rstrip()
                # Everything before the ellipsis must be whole words from the source.
                for word in body.split():
                    self.assertIn(word, f"also noted: {self.TITLE}".split(),
                                  f"cut inside a word at width {width}: {clipped!r}")

    def test_clipping_is_word_safe_in_the_cli(self) -> None:
        from code_shipper.surfaces.cli_view import _clip

        for width in range(8, 60):
            clipped = _clip(f"also noted: {self.TITLE}", width)
            if clipped.endswith("…"):
                body = clipped[:-1].rstrip()
                for word in body.split():
                    self.assertIn(word, f"also noted: {self.TITLE}".split(),
                                  f"cut inside a word at width {width}: {clipped!r}")

    def test_a_different_title_is_named_in_full(self) -> None:
        merged = apply_root_cause_merge(
            self._pair(title="clamp() never applies the upper bound",
                       second_title="clamp() never applies the upper bound either")
        )
        if len(merged) == 1 and merged[0].body:
            self.assertNotIn("…", merged[0].body, "a title in the body must not be truncated")

    def test_the_suffix_is_readable_english(self) -> None:
        suffix = apply_root_cause_merge(self._pair())[0].body.strip()
        self.assertTrue(suffix.startswith("Also noted"), suffix)
        self.assertNotIn("Also noted on this line", suffix,
                         "the phrase claimed a line even when the line was the point of the note")


class LocalCISealTests(unittest.TestCase):
    """The seal gate refused any path containing ".hermes" — the same over-broad rule fix 5 removed.

    A Hermes user's TMPDIR is under ~/.hermes/cache, so the gate failed for every one of them. The gate
    must refuse credential and live-state locations, and a scratch directory under ~/.hermes/cache must
    pass.
    """

    SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "local-ci.sh"

    def _source(self) -> str:
        return self.SCRIPT.read_text()

    def test_the_gate_no_longer_matches_a_bare_dot_hermes(self) -> None:
        source = self._source()
        self.assertNotIn('*".hermes"*', source,
                         "the old over-broad match is still present")

    def test_the_gate_names_the_credential_locations(self) -> None:
        source = self._source()
        for needle in ("/.hermes/auth/", "/.hermes/tokens/", "/.hermes/secrets/",
                       "/.ssh/", "/.aws/"):
            self.assertIn(needle, source, f"the seal gate does not name {needle}")

    def test_the_run_directory_base_is_chosen_and_printed(self) -> None:
        """It must not inherit TMPDIR blindly: that is what put it under ~/.hermes."""
        source = self._source()
        self.assertIn("LOCAL_CI_BASE", source)
        self.assertIn("CI_BASE=", source)
        self.assertIn("run dir:", source, "the base must be printed so a reader can see where it ran")

    def test_the_script_is_valid_bash(self) -> None:
        import subprocess

        result = subprocess.run(["bash", "-n", str(self.SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_scratch_base_under_hermes_cache_passes(self) -> None:
        """Run the gate itself, pointed at ~/.hermes/cache. This is the case that was broken."""
        import os
        import subprocess

        base = Path.home() / ".hermes" / "cache" / "local-ci-test-base"
        base.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, LOCAL_CI_BASE=str(base))
        result = subprocess.run(
            ["bash", str(self.SCRIPT), "--only", "seal"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(result.returncode, 0,
                         f"a scratch dir under ~/.hermes/cache must pass:\n{result.stdout}")
        self.assertIn("PASS", result.stdout)

    def test_a_credential_base_fails(self) -> None:
        """A base that reaches a credential location must still fail. No real credential is touched."""
        import os
        import subprocess

        base = Path.home() / ".hermes" / "auth"
        created = not base.exists()
        try:
            base.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ, LOCAL_CI_BASE=str(base))
            result = subprocess.run(
                ["bash", str(self.SCRIPT), "--only", "seal"],
                capture_output=True, text=True, env=env,
            )
            self.assertNotEqual(result.returncode, 0,
                               "a credential location must fail the seal gate")
            self.assertIn("FAIL", result.stdout)
        finally:
            if created:
                try:
                    base.rmdir()
                except OSError:
                    pass
class ReviewExitCodeContractTests(unittest.TestCase):
    """Fix 2: `review` had its own wrong exit-code rule.

    `return 0 if outcome.complete and not result.paused else 1` meant a review with two important findings
    exited 0 — clean, to any caller reading the code — and an incomplete review exited 1, which reads as
    "findings" and invites a fix loop over a review that never finished.
    """

    def _result(self, *, complete=True, findings=(), errors=(), paused=False):
        """A stand-in review outcome. Named `_result`, not `_outcome`: `_Outcome` is unittest's own type
        and the class definition shadowed it, which failed every test in this class with a TypeError."""
        class Plan:
            to_post = list(findings)

        class Result:
            pass

        result = Result()
        result.complete = complete
        result.errors = list(errors)
        result.findings = list(findings)
        result.plan = Plan()
        result.paused = paused
        return result

    def _code(self, **kwargs) -> int:
        from code_shipper.cli import _review_exit_code

        return _review_exit_code(self._result(**kwargs))

    def test_a_clean_review_exits_zero(self) -> None:
        self.assertEqual(self._code(), 0)

    def test_a_review_with_findings_exits_one(self) -> None:
        finding = type("F", (), {"severity": "important"})()
        self.assertEqual(self._code(findings=[finding]), 1,
                         "findings must never exit 0")

    def test_an_incomplete_review_exits_three(self) -> None:
        self.assertEqual(self._code(complete=False), 3,
                         "incomplete is its own code, not 1 and not 2")

    def test_an_error_exits_two(self) -> None:
        self.assertEqual(self._code(complete=False, errors=["boom"]), 2)

    def test_findings_never_give_zero_whatever_else_is_true(self) -> None:
        finding = type("F", (), {"severity": "critical"})()
        for complete in (True, False):
            for errors in ([], ["boom"]):
                code = self._code(complete=complete, findings=[finding], errors=errors)
                self.assertNotEqual(code, 0, f"complete={complete} errors={bool(errors)} exited 0")

    def test_the_four_codes_are_distinct(self) -> None:
        finding = type("F", (), {"severity": "important"})()
        codes = {
            self._code(),
            self._code(findings=[finding]),
            self._code(complete=False, errors=["boom"]),
            self._code(complete=False),
        }
        self.assertEqual(codes, {0, 1, 2, 3}, f"the contract needs four distinct codes, got {codes}")
class FlagAbbreviationTests(unittest.TestCase):
    """Fix 3: `--pr` expanded to `--profile`, so `review --repo X --pr 25` failed confusingly."""

    def _parser(self):
        from code_shipper import cli

        return cli.build_parser()

    def test_the_main_parser_refuses_abbreviations(self) -> None:
        # argparse stores this on the parser, not the subparser
        parser = self._parser()
        self.assertFalse(getattr(parser, "allow_abbrev", True))

    def test_every_subparser_refuses_abbreviations(self) -> None:
        parser = self._parser()
        subparsers = next(a for a in parser._actions if hasattr(a, "choices") and a.choices)
        for name, sub in subparsers.choices.items():
            self.assertFalse(getattr(sub, "allow_abbrev", True),
                             f"{name} still accepts abbreviated flags")

    def test_pr_resolves_to_pr_not_profile(self) -> None:
        from code_shipper.cli import _resolve_pr_alias

        args = _resolve_pr_alias(self._parser().parse_args(["review", "--repo", "o/r", "--pr", "25"]))
        self.assertEqual(args.pr, 25,
                         "--pr must be the pull request number, not --profile")

    def test_the_positional_form_still_works(self) -> None:
        args = self._parser().parse_args(["review", "25", "--repo", "o/r"])
        self.assertEqual(args.pr, 25)

    def test_both_forms_at_once_is_refused_when_they_disagree(self) -> None:
        parser = self._parser()
        args = parser.parse_args(["review", "25", "--repo", "o/r", "--pr", "26"])
        with self.assertRaises(SystemExit):
            from code_shipper.cli import _resolve_pr_alias

            _resolve_pr_alias(args)

    def test_both_forms_agreeing_is_fine(self) -> None:
        parser = self._parser()
        args = parser.parse_args(["review", "25", "--repo", "o/r", "--pr", "25"])
        from code_shipper.cli import _resolve_pr_alias

        self.assertEqual(_resolve_pr_alias(args).pr, 25)

class DesktopPaletteClosedTests(unittest.TestCase):
    """Fix 4: the palette was drawn permanently open over the page."""

    def _html(self, **kwargs) -> str:
        from code_shipper.surfaces import desktop, fixtures

        return desktop.render(fixtures.state_findings(), **kwargs)

    def test_the_palette_is_closed_by_default(self) -> None:
        html = self._html()
        self.assertIn(".pwrap{display:none", html,
                      "the palette must start closed, not open over the page")

    def test_it_opens_only_when_the_toggle_is_checked(self) -> None:
        html = self._html()
        self.assertIn(":checked ~ .pwrap{display:grid}", html)

    def test_the_ctrl_k_hint_is_the_opener(self) -> None:
        html = self._html()
        self.assertIn('for="palette-toggle"', html)
        self.assertIn('id="palette-toggle"', html)

    def test_the_page_needs_no_script_to_toggle(self) -> None:
        html = self._html()
        self.assertNotIn("<script", html, "a static page must not need scripts")

    def test_the_palette_items_are_still_defined(self) -> None:
        """Closed by default, but the items must still be in the DOM to open."""
        html = self._html()
        self.assertIn("pitem", html)

class DemoBannerGridTests(unittest.TestCase):
    """Fix 5: the demo banner took row 1, pushing the content below the fold."""

    def _html(self, demo: bool) -> str:
        from code_shipper.surfaces import desktop, fixtures

        return desktop.render(fixtures.state_findings(), demo=demo)

    def test_the_demo_window_declares_an_extra_row(self) -> None:
        html = self._html(True)
        self.assertIn(".win.demo{grid-template-rows:", html,
                      "the banner needs its own row or it displaces the header")

    def test_the_demo_window_uses_the_class(self) -> None:
        html = self._html(True)
        self.assertIn('class="win demo"', html)

    def test_the_real_window_has_the_plain_class(self) -> None:
        html = self._html(False)
        self.assertIn('class="win"', html)
        self.assertNotIn('class="win demo"', html)

    def test_the_row_count_matches_the_banner(self) -> None:
        """Four rows with a banner, three without."""
        demo_rows = self._html(True).split(".win.demo{grid-template-rows:")[1].split("}")[0]
        self.assertEqual(len(demo_rows.strip().split()), 4, demo_rows)
        real_rows = self._html(False).split("grid-template-rows:")[1].split(";")[0]
        self.assertEqual(len(real_rows.strip().split()), 3, real_rows)


class ParallelMeasurementRunnerTests(unittest.TestCase):
    """The parallel runner must isolate seeds and never report a rate it did not measure.

    The sequential runner needed ~6 hours for 20 seeds because one slow lens blocked everything behind it.
    The seeds share nothing, so they run concurrently — but only if the isolation actually holds.
    """

    SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run_measurement_parallel.py"

    def _source(self) -> str:
        return self.SCRIPT.read_text()

    def test_each_worker_builds_its_own_harness(self) -> None:
        """A shared harness would serialise on one subprocess and blur failure attribution."""
        source = self._source()
        self.assertIn("def build_harness", source)
        # The harness must be constructed inside the worker, not passed in from the parent.
        worker = source.split("def run_one(")[1].split("def main(")[0]
        self.assertIn("build_harness(", worker, "the harness must be per-worker")

    def test_each_seed_gets_its_own_directory(self) -> None:
        worker = self._source().split("def run_one(")[1].split("def main(")[0]
        self.assertIn("work_root / seed.slug", worker)

    def test_a_failing_seed_does_not_end_the_run(self) -> None:
        source = self._source()
        worker = source.split("def run_one(")[1].split("def main(")[0]
        self.assertIn("except Exception", worker, "one seed raising must be caught")
        self.assertIn('"status": "error"', worker)

    def test_a_timed_out_lens_is_excluded_not_counted_clean(self) -> None:
        worker = self._source().split("def run_one(")[1].split("def main(")[0]
        self.assertIn("unevaluated", worker)
        self.assertIn("outcome.errors", worker)

    def test_a_failed_control_is_discarded(self) -> None:
        worker = self._source().split("def run_one(")[1].split("def main(")[0]
        self.assertIn("discarded", worker)

    def test_the_rate_is_none_when_nothing_was_evaluated(self) -> None:
        """The guard that stopped a broken harness reporting 0% in the last run."""
        source = self._source()
        self.assertIn('"seeds_with_findings_rate": (seeds_with_findings / len(evaluated))', source)

    def test_results_are_ordered_by_seed_not_completion(self) -> None:
        source = self._source()
        self.assertIn("results.sort", source)

    def test_the_default_worker_count_is_the_measured_one(self) -> None:
        """4 workers times out every lens; 2 works. The default must be the number that measures.

        Measured: `--workers 4` gave 4/4 UNEVALUATED on two separate batches (`harness timed out` on
        every lens), while `--workers 2` gave 2/2 evaluated. Concurrency past the provider's tolerance
        does not measure more, it measures nothing.
        """
        source = self._source()
        self.assertIn('"--workers", type=int, default=2', source)

    def test_unevaluated_errors_are_preserved_in_the_json(self) -> None:
        """An unevaluated seed with no reason recorded is useless as evidence."""
        source = self._source()
        self.assertIn("unevaluated_detail", source)

    def test_it_parses(self) -> None:
        import ast

        ast.parse(self._source())

    def test_the_dry_run_is_green(self) -> None:
        """Every seed still passes its own control after the six fixes."""
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, str(self.SCRIPT), "--dry-run"],
            capture_output=True, text=True, timeout=600,
            cwd=str(self.SCRIPT.parent.parent),
        )
        self.assertEqual(result.returncode, 0, result.stdout[-800:] + result.stderr[-400:])
        self.assertIn("20 clean, 0 discarded", result.stdout)
