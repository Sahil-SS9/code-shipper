"""Phase 5: secret scanning, the ship flow, and polling triggers.

The gates that matter here, and what each test pins:

* a secret blocks before anything is written, and the report never contains the secret;
* publishing without a recorded authorisation fails with a message, in a non-TTY run, without hanging;
* one commit, authored as the configured identity;
* a new head is detected by polling, and a stop is honoured before any work in that round;
* the first poll establishes a baseline without reviewing.
"""

from __future__ import annotations

import inspect
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from code_shipper import secrets as secrets_mod
from code_shipper import ship as ship_mod
from code_shipper.steward import poll as poll_mod


# Fake credentials, constructed so this file does not itself trip a scan. Never real values.
FAKE_GH = "gh" + "p_" + "A" * 36
FAKE_AWS = "AKIA" + "B" * 16
FAKE_KEY = "-----BEGIN " + "RSA PRIVATE KEY-----"


class SecretScanTests(unittest.TestCase):
    """T-UNIT-SECRETS-01..10."""

    def test_a_github_token_is_found(self) -> None:
        findings = secrets_mod.scan_text(f"token: {FAKE_GH}", location="a.py")
        self.assertTrue(findings)
        self.assertEqual(findings[0].pattern, "github-token")

    def test_a_private_key_is_found(self) -> None:
        self.assertTrue(secrets_mod.scan_text(FAKE_KEY))

    def test_an_aws_key_is_found(self) -> None:
        self.assertTrue(secrets_mod.scan_text(f"id = '{FAKE_AWS}'"))

    def test_a_credential_assignment_is_found(self) -> None:
        findings = secrets_mod.scan_text('api_key = "abcdef1234567890abcdef"')
        self.assertTrue(any(f.pattern == "generic-assignment" for f in findings))

    def test_a_url_with_a_password_is_found(self) -> None:
        self.assertTrue(secrets_mod.scan_text("https://user:hunter2pass@example.com/x"))

    def test_clean_text_finds_nothing(self) -> None:
        self.assertEqual(secrets_mod.scan_text("x = compute(a, b)\n# no secrets here"), [])

    def test_the_placeholder_is_exempt(self) -> None:
        # the repository's own tests use this string; it must not block them
        self.assertEqual(secrets_mod.scan_text("key = 'placeholder-not-a-credential'"), [])

    def test_a_finding_never_contains_the_secret(self) -> None:
        findings = secrets_mod.scan_text(f"token: {FAKE_GH}", location="a.py")
        payload = secrets_mod.report(findings)
        self.assertNotIn(FAKE_GH, payload)
        self.assertNotIn(FAKE_GH, str(findings[0].as_dict()))
        self.assertIn(secrets_mod.REDACTED, payload)

    def test_the_mask_is_not_the_key_length(self) -> None:
        short = secrets_mod._mask("abcdefghij")
        self.assertEqual(
            short, f"ab{secrets_mod.REDACTED}",
            "the mask reveals two characters and nothing about the rest",
        )

    def test_redaction_cleans_arbitrary_output(self) -> None:
        text = f"diff --git a/x b/x\n+token = '{FAKE_GH}'\n"
        redacted = secrets_mod.redact(text)
        self.assertNotIn(FAKE_GH, redacted)
        self.assertIn(secrets_mod.REDACTED, redacted)

    def test_redaction_keeps_clean_text_intact(self) -> None:
        text = "def divide(a, b):\n    return a / b\n"
        self.assertEqual(secrets_mod.redact(text), text)

    def test_a_finding_blocks(self) -> None:
        with self.assertRaises(secrets_mod.SecretFound):
            secrets_mod.assert_clean({"a.py": f"k = '{FAKE_GH}'"})

    def test_a_clean_set_does_not_raise(self) -> None:
        secrets_mod.assert_clean({"a.py": "x = 1\n"})

    def test_scanning_reports_the_location(self) -> None:
        findings = secrets_mod.scan_files({"src/config.py": f"k='{FAKE_GH}'"})
        self.assertEqual(findings[0].location, "src/config.py")
        self.assertEqual(findings[0].line, 1)

    def test_a_diff_finding_comes_from_an_added_line(self) -> None:
        patch = f"@@\n+key = '{FAKE_GH}'\n-removed = clean\n"
        findings = secrets_mod.scan_diff(patch, path="a.py")
        self.assertEqual(len(findings), 1)

    def test_a_removal_alone_does_not_block_forever(self) -> None:
        # a rotated key being removed must not block a later commit
        patch = f"@@\n-key = '{FAKE_GH}'\n+key = os.environ['X']\n"
        self.assertEqual(secrets_mod.scan_diff(patch, path="a.py"), [])

    def test_the_report_says_publishing_must_not_continue(self) -> None:
        findings = secrets_mod.scan_text(FAKE_GH)
        self.assertIn("must not continue", secrets_mod.report(findings))

    def test_the_report_tells_the_reader_to_rotate(self) -> None:
        findings = secrets_mod.scan_text(FAKE_GH)
        self.assertIn("rotate", secrets_mod.report(findings))

    def test_a_private_key_block_is_caught_even_multiline(self) -> None:
        text = f"{FAKE_KEY}\nMIIEow...\n-----END RSA PRIVATE KEY-----"
        self.assertTrue(secrets_mod.scan_text(text))


class PlanTests(unittest.TestCase):
    def _plan(self, **overrides) -> "ship_mod.Plan":
        kwargs = dict(
            repo="o/r", branch="feature", base="main", title="Add a thing",
            files={"a.py": "x = 1\n"}, author_name="Sahil Saghir",
            author_email="218421507+Sahil-SS9@users.noreply.github.com",
        )
        kwargs.update(overrides)
        return ship_mod.make_plan(**kwargs)

    def test_a_valid_plan_is_built(self) -> None:
        plan = self._plan()
        self.assertEqual(plan.branch, "feature")

    def test_a_missing_base_is_refused(self) -> None:
        with self.assertRaises(ship_mod.ShipRefused):
            self._plan(base="")

    def test_branch_equal_to_base_is_refused(self) -> None:
        with self.assertRaises(ship_mod.ShipRefused) as caught:
            self._plan(branch="main", base="main")
        self.assertEqual(caught.exception.code, "branch_is_base")

    def test_a_missing_author_is_refused(self) -> None:
        # a commit with no author does not count towards the contribution graph
        with self.assertRaises(ship_mod.ShipRefused) as caught:
            self._plan(author_email="")
        self.assertEqual(caught.exception.code, "no_author")

    def test_an_empty_change_is_refused(self) -> None:
        with self.assertRaises(ship_mod.ShipRefused):
            self._plan(files={})

    def test_the_plan_names_every_file(self) -> None:
        plan = self._plan(files={"a.py": "x", "b/c.py": "y"})
        text = plan.render()
        self.assertIn("a.py", text)
        self.assertIn("b/c.py", text)

    def test_the_plan_says_it_will_not_merge(self) -> None:
        self.assertIn("will not merge", self._plan().render())


class AuthorisationTests(unittest.TestCase):
    """The gate: no publication without a recorded authorisation, and no hanging."""

    def test_no_authorisation_does_not_grant(self) -> None:
        self.assertFalse(ship_mod.Authorisation.from_flag(None).granted)

    def test_a_non_affirmative_value_does_not_grant(self) -> None:
        self.assertFalse(ship_mod.Authorisation.from_flag("no").granted)
        self.assertFalse(ship_mod.Authorisation.from_flag("maybe").granted)

    def test_an_affirmative_value_grants(self) -> None:
        self.assertTrue(ship_mod.Authorisation.from_flag("i-authorise-publish").granted)

    def test_require_raises_with_an_explanation(self) -> None:
        with self.assertRaises(ship_mod.ShipRefused) as caught:
            ship_mod.Authorisation(granted=False).require()
        self.assertEqual(caught.exception.code, "not_authorised")
        self.assertIn("--i-authorise-publish", caught.exception.detail)

    def test_requiring_twice_is_idempotent(self) -> None:
        ship_mod.Authorisation(granted=True).require()
        ship_mod.Authorisation(granted=True).require()


class ShipFlowTests(unittest.TestCase):
    """End to end against a throwaway local repo — real git, no network."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cs-ship-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        # a real bare remote, so the push in the flow is a real push
        origin = self.root / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self._git("init", "-q", "-b", "main")
        self._git("remote", "add", "origin", str(origin))
        (self.repo / "README.md").write_text("hello\n")
        self._git("add", ".")
        self._git("commit", "-q", "-m", "initial")

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=t@example.com", *args],
            cwd=str(self.repo), capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def _plan(self, files=None, **overrides):
        kwargs = dict(
            repo="o/r", branch="feature", base="main", title="Add a thing",
            files=files if files is not None else {"calc.py": "def f():\n    return 1\n"},
            author_name="Sahil Saghir",
            author_email="218421507+Sahil-SS9@users.noreply.github.com",
        )
        kwargs.update(overrides)
        return ship_mod.make_plan(**kwargs)

    class _Broker:
        def __init__(self) -> None:
            self.created = []
            self.requests = 0

        def create_pr(self, **kwargs):
            self.created.append(kwargs)
            return {"number": 42, "html_url": "https://example.invalid/pr/42"}

    def test_a_secret_blocks_before_any_write(self) -> None:
        plan = self._plan(files={"config.py": f"key = '{FAKE_GH}'\n"})
        with self.assertRaises(secrets_mod.SecretFound):
            ship_mod.ship(plan, workdir=self.repo,
                          authorisation=ship_mod.Authorisation(granted=True))
        # nothing was written and no branch was created
        self.assertFalse((self.repo / "config.py").exists())
        self.assertEqual(self._git("branch", "--list", "feature").strip(), "")

    def test_no_authorisation_blocks_before_a_branch(self) -> None:
        plan = self._plan()
        with self.assertRaises(ship_mod.ShipRefused) as caught:
            ship_mod.ship(plan, workdir=self.repo,
                          authorisation=ship_mod.Authorisation.from_flag(None))
        self.assertEqual(caught.exception.code, "not_authorised")
        self.assertEqual(self._git("branch", "--list", "feature").strip(), "")

    def test_a_dry_run_writes_nothing(self) -> None:
        plan = self._plan()
        result = ship_mod.ship(plan, workdir=self.repo,
                               authorisation=ship_mod.Authorisation(granted=True), dry_run=True)
        self.assertFalse(result.pushed)
        self.assertEqual(self._git("branch", "--list", "feature").strip(), "")

    def test_a_ship_run_commits_with_the_configured_author(self) -> None:
        plan = self._plan()
        result = ship_mod.ship(plan, workdir=self.repo,
                               authorisation=ship_mod.Authorisation(granted=True))
        self.assertTrue(result.pushed)
        author = self._git("log", "-1", "--format=%an <%ae>").strip()
        self.assertEqual(author, "Sahil Saghir <218421507+Sahil-SS9@users.noreply.github.com>")

    def test_the_commit_is_one_commit(self) -> None:
        before = self._git("rev-list", "--count", "HEAD").strip()
        plan = self._plan()
        ship_mod.ship(plan, workdir=self.repo,
                      authorisation=ship_mod.Authorisation(granted=True))
        after = self._git("rev-list", "--count", "HEAD").strip()
        self.assertEqual(int(after), int(before) + 1)

    def test_the_pr_is_opened_through_the_broker(self) -> None:
        broker = self._Broker()
        plan = self._plan()
        result = ship_mod.ship(plan, workdir=self.repo,
                               authorisation=ship_mod.Authorisation(granted=True), broker=broker)
        self.assertEqual(result.pr, 42)
        self.assertEqual(broker.created[0]["head"], "feature")
        self.assertEqual(broker.created[0]["base"], "main")

    def test_the_steps_run_in_order(self) -> None:
        broker = self._Broker()
        result = ship_mod.ship(self._plan(), workdir=self.repo,
                               authorisation=ship_mod.Authorisation(granted=True), broker=broker)
        self.assertEqual(result.steps_done,
                         ["plan", "scan", "authorise", "branch", "commit", "push", "pr"])

    def test_the_result_has_no_merge_field(self) -> None:
        result = ship_mod.ship(self._plan(), workdir=self.repo,
                               authorisation=ship_mod.Authorisation(granted=True),
                               broker=self._Broker())
        payload = result.as_dict()
        for key in ("merged", "merge", "approved"):
            self.assertNotIn(key, payload)

    def test_the_ship_module_has_no_merge_call(self) -> None:
        # Checked as operations, not as substrings: the module's prose says "never merges", and a
        # substring check flags its own promise.
        source = inspect.getsource(ship_mod)
        self.assertNotIn("merge(", source)
        self.assertNotIn('"merge"', source)
        self.assertNotIn("APPROVE", source)
        self.assertNotIn("REQUEST_CHANGES", source)


class PollTests(unittest.TestCase):
    """D12: our own clock, stop checked each round, no webhooks."""

    def _heads(self, sequence):
        state = {"index": 0}

        def read() -> str:
            value = sequence[min(state["index"], len(sequence) - 1)]
            state["index"] += 1
            return value

        return read

    def test_the_first_poll_is_a_baseline_and_reviews_nothing(self) -> None:
        reviewed: list[tuple[str, str]] = []
        result = poll_mod.poll(
            current_head=lambda: "aaa",
            is_stopped=lambda: False,
            on_new_head=lambda old, new: reviewed.append((old, new)),
            interval=0.01, max_rounds=1, sleep=lambda _s: None,
        )
        self.assertEqual(reviewed, [], "a baseline review was not asked for")
        self.assertEqual(result.rounds[0].reason, "baseline")

    def test_a_new_head_is_detected_and_reviewed_once(self) -> None:
        reviewed: list[tuple[str, str]] = []
        result = poll_mod.poll(
            current_head=self._heads(["aaa", "aaa", "bbb"]),
            is_stopped=lambda: False,
            on_new_head=lambda old, new: reviewed.append((old, new)),
            interval=0.01, max_rounds=3, sleep=lambda _s: None,
        )
        self.assertEqual(reviewed, [("aaa", "bbb")])

    def test_the_same_head_twice_does_not_re_review(self) -> None:
        reviewed: list[tuple[str, str]] = []
        poll_mod.poll(
            current_head=lambda: "aaa",
            is_stopped=lambda: False,
            on_new_head=lambda old, new: reviewed.append((old, new)),
            interval=0.01, max_rounds=4, sleep=lambda _s: None,
        )
        self.assertEqual(reviewed, [])

    def test_a_stop_is_honoured_before_any_work_in_that_round(self) -> None:
        reads = {"count": 0}

        def current_head() -> str:
            reads["count"] += 1
            return "aaa"

        stops = {"count": 0}

        def is_stopped() -> bool:
            stops["count"] += 1
            return stops["count"] >= 2

        result = poll_mod.poll(
            current_head=current_head,
            is_stopped=is_stopped,
            on_new_head=lambda old, new: None,
            interval=0.01, max_rounds=10, sleep=lambda _s: None,
        )
        self.assertEqual(result.stopped_reason, "stopped")
        self.assertEqual(reads["count"], 1, "the stop was checked before reading the head again")
        self.assertTrue(result.rounds[-1].stopped)

    def test_a_stop_in_the_first_round_does_nothing_at_all(self) -> None:
        def explode() -> str:
            raise AssertionError("the head must not be read once stopped")

        result = poll_mod.poll(
            current_head=explode, is_stopped=lambda: True,
            on_new_head=lambda old, new: None, interval=0.01, max_rounds=5,
            sleep=lambda _s: None,
        )
        self.assertEqual(result.stopped_reason, "stopped")

    def test_the_deadline_stops_the_loop_without_an_event(self) -> None:
        ticks = {"now": 0.0}

        def clock() -> float:
            ticks["now"] += 0.5
            return ticks["now"]

        result = poll_mod.poll(
            current_head=lambda: "aaa", is_stopped=lambda: False,
            on_new_head=lambda old, new: None, interval=0.01, minutes=0.01,  # 0.6s
            clock=clock, sleep=lambda _s: None,
        )
        self.assertIn(result.stopped_reason, ("deadline", "max_rounds"))
        self.assertEqual(result.stopped_reason, "deadline")

    def test_a_head_read_failure_does_not_kill_the_loop(self) -> None:
        calls = {"count": 0}

        def flaky() -> str:
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("network hiccup")
            return "aaa"

        result = poll_mod.poll(
            current_head=flaky, is_stopped=lambda: False,
            on_new_head=lambda old, new: None, interval=0.01, max_rounds=3,
            sleep=lambda _s: None,
        )
        self.assertTrue(any("could not read the head" in round_.reason for round_ in result.rounds))

    def test_an_invalid_interval_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            poll_mod.poll(
                current_head=lambda: "a", is_stopped=lambda: False,
                on_new_head=lambda old, new: None, interval=0,
            )

    def test_the_result_records_what_each_round_did(self) -> None:
        result = poll_mod.poll(
            current_head=self._heads(["aaa", "bbb"]), is_stopped=lambda: False,
            on_new_head=lambda old, new: None, interval=0.01, max_rounds=2,
            sleep=lambda _s: None,
        )
        payload = result.as_dict()
        self.assertEqual(payload["reviewed_count"], 1)
        self.assertEqual(len(payload["rounds"]), 2)


if __name__ == "__main__":
    unittest.main()
