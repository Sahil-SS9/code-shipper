"""CodeHost contract tests (T-CONTRACT-02): recorded responses, no network.

The adapter's job is to turn GitHub's payloads into this project's dataclasses without inventing
anything. The fake-transport tests check that mapping and the error handling; the recorded-host tests
check that the interface is satisfied and that a missing fixture fails loudly rather than silently.
"""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from pathlib import Path
from typing import Any

from code_shipper.hosts.base import (
    CheckResult,
    CodeHost,
    Comment,
    Diff,
    FilePatch,
    HostError,
    PullRequest,
    Review,
    split_slug,
)
from code_shipper.hosts.github import GitHubHost, RecordedHost


class FakeOpener:
    """A stand-in for urllib's opener: returns a scripted body or raises a scripted error."""

    def __init__(self, *, body: Any = None, raw: bytes | None = None, error: Exception | None = None):
        self.body = body
        self.raw = raw
        self.error = error
        self.requests: list[Any] = []

    def open(self, request, timeout=None):  # noqa: ANN001, ANN201
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        payload = self.raw if self.raw is not None else json.dumps(self.body).encode()

        class Response(io.BytesIO):
            headers: dict[str, str] = {}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return Response(payload)


def host_with(**kwargs) -> GitHubHost:
    return GitHubHost("tok", opener=FakeOpener(**kwargs))


PR_PAYLOAD = {
    "number": 12,
    "title": "Fix the thing",
    "user": {"login": "author"},
    "head": {"sha": "abc123", "ref": "feature", "repo": {"full_name": "o/r"}},
    "base": {"ref": "main", "repo": {"full_name": "o/r"}},
    "draft": False,
    "labels": [{"name": "bug"}, {"name": "bot"}],
    "state": "open",
}


class PullRequestMappingTests(unittest.TestCase):
    def test_pr_is_mapped_to_the_dataclass(self) -> None:
        pr = host_with(body=PR_PAYLOAD).get_pr("o/r", 12)
        self.assertEqual(pr.number, 12)
        self.assertEqual(pr.author, "author")
        self.assertEqual(pr.head_sha, "abc123")
        self.assertEqual(pr.labels, ("bug", "bot"))
        self.assertFalse(pr.from_fork)

    def test_a_fork_pr_is_flagged(self) -> None:
        payload = json.loads(json.dumps(PR_PAYLOAD))
        payload["head"]["repo"]["full_name"] = "someone-else/r"
        self.assertTrue(host_with(body=payload).get_pr("o/r", 12).from_fork)

    def test_malformed_payload_is_an_error_not_an_empty_pr(self) -> None:
        with self.assertRaises(HostError):
            host_with(body=["not", "a", "pr"]).get_pr("o/r", 12)


class DiffMappingTests(unittest.TestCase):
    def test_files_are_mapped_with_renames(self) -> None:
        body = [
            {"filename": "a.py", "status": "modified", "patch": "@@ -1 +1 @@\n-x\n+y\n"},
            {"filename": "b.py", "status": "renamed", "previous_filename": "old.py"},
        ]
        diff = host_with(body=body).get_diff("o/r", 1)
        self.assertEqual(len(diff.files), 2)
        self.assertEqual(diff.files[1].old_path, "old.py")
        self.assertEqual(diff.files[1].display_path, "old.py -> b.py")

    def test_additions_and_deletions_are_counted_from_the_patch(self) -> None:
        body = [{"filename": "a.py", "patch": "@@\n+one\n+two\n-removed\n context\n"}]
        diff = host_with(body=body).get_diff("o/r", 1)
        self.assertEqual(diff.additions, 2)
        self.assertEqual(diff.deletions, 1)


class ChecksMappingTests(unittest.TestCase):
    def test_check_runs_are_mapped(self) -> None:
        body = {
            "check_runs": [
                {"name": "pytest", "status": "completed", "conclusion": "failure", "id": 5},
                {"name": "lint", "status": "in_progress", "conclusion": None},
            ]
        }
        checks = host_with(body=body).get_checks("o/r", "abc")
        self.assertEqual(len(checks), 2)
        self.assertTrue(checks[0].failed)
        self.assertFalse(checks[1].failed)

    def test_failure_conclusions_count_as_failed(self) -> None:
        for conclusion in ("failure", "timed_out", "cancelled"):
            with self.subTest(conclusion=conclusion):
                check = CheckResult(name="x", status="completed", conclusion=conclusion)
                self.assertTrue(check.failed)

    def test_success_and_skipped_do_not_count_as_failed(self) -> None:
        for conclusion in ("success", "neutral", "skipped"):
            with self.subTest(conclusion=conclusion):
                check = CheckResult(name="x", status="completed", conclusion=conclusion)
                self.assertFalse(check.failed)


class ErrorHandlingTests(unittest.TestCase):
    def test_a_server_error_is_retryable(self) -> None:
        opener = FakeOpener(error=urllib.error.HTTPError("u", 502, "bad gateway", {}, None))  # type: ignore[arg-type]
        with self.assertRaises(HostError) as caught:
            GitHubHost("tok", opener=opener).get_pr("o/r", 1)
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(caught.exception.status, 502)

    def test_a_not_found_is_not_retryable(self) -> None:
        opener = FakeOpener(error=urllib.error.HTTPError("u", 404, "nope", {}, None))  # type: ignore[arg-type]
        with self.assertRaises(HostError) as caught:
            GitHubHost("tok", opener=opener).get_pr("o/r", 1)
        self.assertFalse(caught.exception.retryable)

    def test_a_missing_file_returns_none_rather_than_raising(self) -> None:
        opener = FakeOpener(error=urllib.error.HTTPError("u", 404, "nope", {}, None))  # type: ignore[arg-type]
        self.assertIsNone(GitHubHost("tok", opener=opener).read_file("o/r", "main", "x.py"))

    def test_malformed_json_is_reported(self) -> None:
        with self.assertRaises(HostError):
            host_with(raw=b"{not json").get_pr("o/r", 1)

    def test_the_token_is_never_in_the_repr(self) -> None:
        secret = "supersecret-token-value"
        self.assertNotIn(secret, repr(GitHubHost(secret)))
        self.assertIn("redacted", repr(GitHubHost(secret)))

    def test_an_empty_token_is_refused(self) -> None:
        with self.assertRaises(HostError):
            GitHubHost("")


class ReadOnlyTests(unittest.TestCase):
    """The adapter must have no write surface at all."""

    def test_no_method_name_suggests_a_write(self) -> None:
        forbidden = ("create", "post", "merge", "approve", "delete", "update", "put", "patch", "push")
        for name in dir(GitHubHost):
            if name.startswith("_"):
                continue
            lowered = name.lower()
            for word in forbidden:
                self.assertNotIn(word, lowered, f"{name} looks like a write operation")

    def test_the_protocol_declares_only_reads(self) -> None:
        methods = [n for n in dir(CodeHost) if not n.startswith("_")]
        self.assertTrue(any(n.startswith("get_") for n in methods))


class SlugTests(unittest.TestCase):
    def test_valid_slugs_parse(self) -> None:
        self.assertEqual(split_slug("owner/name"), ("owner", "name"))

    def test_path_like_slugs_are_refused(self) -> None:
        for bad in ("a/b/c", "..", "o/..", "", "o/", "/r", "o/r?x=1", "o/r/../../etc"):
            with self.subTest(slug=bad):
                with self.assertRaises(HostError):
                    split_slug(bad)


class RecordedHostTests(unittest.TestCase):
    """T-CONTRACT-02 offline half, plus the fixture-missing behaviour."""

    def test_recorded_responses_satisfy_the_interface(self) -> None:
        host = RecordedHost(
            {
                "get_pr": PullRequest(1, "t", "a", "sha", "h", "main"),
                "get_diff": Diff(files=(FilePatch("a.py"),)),
                "get_checks": [CheckResult("pytest", "completed", "success")],
                "list_comments": [Comment(1, "a", "hi")],
                "list_reviews": [Review(1, "a", "APPROVED")],
                "permission_of": "write",
                "read_file": b"data",
                "default_branch": "main",
            }
        )
        self.assertEqual(host.get_pr("o/r", 1).number, 1)
        self.assertEqual(host.get_diff("o/r", 1).files[0].path, "a.py")
        self.assertEqual(host.get_checks("o/r", "sha")[0].name, "pytest")
        self.assertEqual(host.list_comments("o/r", 1)[0].body, "hi")
        self.assertEqual(host.list_reviews("o/r", 1)[0].state, "APPROVED")
        self.assertEqual(host.permission_of("o/r", "a"), "write")
        self.assertEqual(host.read_file("o/r", "main", "x"), b"data")
        self.assertEqual(host.default_branch("o/r"), "main")

    def test_a_missing_fixture_fails_loudly(self) -> None:
        # a test must not pass by accident on an absent recording
        with self.assertRaises(HostError):
            RecordedHost({}).get_pr("o/r", 1)

    def test_calls_are_recorded_for_assertions(self) -> None:
        host = RecordedHost({"get_pr": PullRequest(1, "t", "a", "s", "h", "main")})
        host.get_pr("o/r", 1)
        self.assertEqual(host.calls[0][0], "get_pr")


if __name__ == "__main__":
    unittest.main()
