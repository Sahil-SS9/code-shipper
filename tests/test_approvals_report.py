"""N7: `approvals report` — the current policy's answer next to what reviewers actually did."""

from __future__ import annotations

import contextlib
import io
import json
import time
import unittest
from dataclasses import dataclass
from unittest import mock

from code_shipper.approvals.policy import parse
from code_shipper.approvals.report import (
    AGREE_APPROVE,
    AGREE_HOLD,
    TOO_LOOSE,
    TOO_STRICT,
    build_report,
    classify,
    human_outcome,
    parse_since,
)
from code_shipper.hosts.base import Diff, FilePatch, PullRequest

POLICY_TEXT = """
version: 1
approvals:
  - count: 1
    approvers: [alice, bob]
    observe: true
  - count: 2
    approvers: [alice, bob, carol]
    files: ["billing/**"]
    observe: true
"""


@dataclass
class R:
    author: str
    state: str


def pr(number: int, title: str = "t") -> PullRequest:
    return PullRequest(number=number, title=title, author="dev", head_sha="h", head_ref="f", base_ref="main",
                       state="closed")


class CasesTests(unittest.TestCase):
    def test_the_four_cases(self) -> None:
        self.assertEqual(classify(True, "changes"), TOO_LOOSE)
        self.assertEqual(classify(False, "approved"), TOO_STRICT)
        self.assertEqual(classify(True, "approved"), AGREE_APPROVE)
        self.assertEqual(classify(False, "changes"), AGREE_HOLD)
        self.assertEqual(classify(False, "none"), AGREE_HOLD)

    def test_each_reviewers_latest_decision_counts(self) -> None:
        self.assertEqual(human_outcome([R("alice", "CHANGES_REQUESTED"), R("alice", "APPROVED")]),
                         ("approved", ("alice",)))
        self.assertEqual(human_outcome([R("alice", "APPROVED"), R("bob", "CHANGES_REQUESTED")]),
                         ("changes", ("alice",)))
        self.assertEqual(human_outcome([R("alice", "COMMENTED")]), ("none", ()))
        self.assertEqual(human_outcome([R("alice", "APPROVED"), R("alice", "DISMISSED")]), ("none", ()))

    def test_since_is_parsed_or_refused(self) -> None:
        self.assertAlmostEqual(parse_since("14d", now=1_000_000), 1_000_000 - 14 * 86400)
        self.assertAlmostEqual(parse_since("2w", now=1_000_000), 1_000_000 - 14 * 86400)
        self.assertAlmostEqual(parse_since("36h", now=1_000_000), 1_000_000 - 36 * 3600)
        for bad in ("14", "two weeks", "-1d", ""):
            with self.assertRaises(ValueError):
                parse_since(bad)


class TheReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = parse(POLICY_TEXT, source="base")

    def test_rows_land_in_the_right_case_with_a_real_policy(self) -> None:
        merged = [
            # one approval, ordinary file: satisfied; but bob asked for changes -> too loose
            {"pr": pr(1), "files": ["app.py"], "reviews": [R("alice", "APPROVED"), R("bob", "CHANGES_REQUESTED")]},
            # billing needs two; one given and the reviewer approved -> too strict
            {"pr": pr(2), "files": ["billing/total.py"], "reviews": [R("alice", "APPROVED")]},
            # agree, approved
            {"pr": pr(3), "files": ["app.py"], "reviews": [R("alice", "APPROVED")]},
            # nobody reviewed: not satisfied, held
            {"pr": pr(4), "files": ["app.py"], "reviews": []},
            {"pr": pr(5), "error": "rate limited"},
        ]
        report = build_report(self.policy, merged)
        cases = {row.number: row.case for row in report.rows}
        self.assertEqual(cases, {1: TOO_LOOSE, 2: TOO_STRICT, 3: AGREE_APPROVE, 4: AGREE_HOLD})
        self.assertEqual(report.counts()[TOO_LOOSE], 1)
        self.assertEqual(report.errors, ["#5: rate limited"])
        text = report.render()
        self.assertIn("fix these first", text)
        self.assertIn("#1", text)
        self.assertIn("Code-Shipper never merges. Merge on GitHub.", text)

    def test_observe_only_rules_are_judged_as_if_enforced(self) -> None:
        """That is the point of observe mode: the report says what enforcing them would have done."""
        report = build_report(self.policy, [{"pr": pr(2), "files": ["billing/x.py"],
                                             "reviews": [R("alice", "APPROVED")]}])
        self.assertTrue(report.rows[0].policy.startswith("not satisfied"))

    def test_an_unevaluable_policy_is_reported_not_counted(self) -> None:
        report = build_report(None, [{"pr": pr(1), "files": [], "reviews": []}])
        self.assertEqual(report.rows, [])
        self.assertTrue(report.errors)


class TheCommandTests(unittest.TestCase):
    def test_read_only_end_to_end_with_a_fake_host(self) -> None:
        from code_shipper import cli

        host = mock.Mock()
        host.default_branch.return_value = "main"
        host.resolve_ref.return_value = "abc1234def"
        host.read_file.side_effect = lambda repo, ref, path: POLICY_TEXT.encode() if path == "code-shipper.yml" else None
        host.list_landed_prs.return_value = [pr(1)]
        host.get_diff.return_value = Diff(files=(FilePatch(path="app.py"),))
        host.list_reviews.return_value = [R("alice", "APPROVED"), R("bob", "CHANGES_REQUESTED")]
        out = io.StringIO()
        with mock.patch.object(cli, "_approvals_host", return_value=host), \
                mock.patch.dict("os.environ", {"CODE_SHIPPER_GITHUB_TOKEN": "t"}), \
                contextlib.redirect_stdout(out):
            code = cli.main(["approvals", "report", "--repo", "o/n", "--since", "14d", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["counts"][TOO_LOOSE], 1)
        self.assertEqual(payload["policy_ref"], "abc1234def")
        host.read_file.assert_any_call("o/n", "abc1234def", "code-shipper.yml")
        for write in ("post_comment", "update_comment", "add_label", "create_review", "merge"):
            self.assertFalse(getattr(host, write).called, write)
        since = host.list_landed_prs.call_args[0][1]
        self.assertAlmostEqual(since, time.time() - 14 * 86400, delta=60)

    def test_a_bad_window_and_no_policy_exit_2(self) -> None:
        from code_shipper import cli

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["approvals", "report", "--repo", "o/n", "--since", "soon"]), 2)
        host = mock.Mock()
        host.default_branch.return_value = "main"
        host.resolve_ref.return_value = "abc"
        host.read_file.return_value = None
        with mock.patch.object(cli, "_approvals_host", return_value=host), \
                mock.patch.dict("os.environ", {"CODE_SHIPPER_GITHUB_TOKEN": "t"}), \
                contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["approvals", "report", "--repo", "o/n"]), 2)
        self.assertIn("no approvals policy", err.getvalue())


class TheHostReaderTests(unittest.TestCase):
    def test_only_merged_prs_inside_the_window_are_returned(self) -> None:
        from code_shipper.hosts.github import GitHubHost

        def iso(seconds_ago: int) -> str:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds_ago))

        page = [
            {"number": 1, "merged_at": iso(3600), "updated_at": iso(3600)},
            {"number": 2, "merged_at": None, "updated_at": iso(3600)},
            {"number": 3, "merged_at": iso(30 * 86400), "updated_at": iso(30 * 86400)},
        ]
        host = GitHubHost("t")
        with mock.patch.object(host, "_get", return_value=(page, {})) as get:
            found = host.list_landed_prs("o/n", time.time() - 14 * 86400)
        self.assertEqual([p.number for p in found], [1])
        self.assertIn("state=closed", get.call_args[0][0])
        self.assertEqual(get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
