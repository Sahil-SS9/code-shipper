"""Broker write-path tests, including T-ADV-13.

T-ADV-13 is proven here and not at GitHub. The posting token belongs to the PR author, so GitHub
would refuse a self-approval with a 422 whatever we did — that would prove GitHub's rule, not ours.
The claim is that Code-Shipper's own broker refuses an APPROVE before it builds a request, and the
test for that counts outbound requests: zero, for every refused event.

A test that only checked the raised exception would pass even if the request had already been sent.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from code_shipper.broker.write import (
    REVIEWER_EVENTS,
    Broker,
    BrokerError,
    BrokerRefused,
    CountingTransport,
)

REPO = "Sahil-SS9/code-shipper-sandbox"


def broker(**kwargs) -> tuple[Broker, CountingTransport]:
    transport = kwargs.pop("transport", None) or CountingTransport()
    return Broker(token="ghp_faketoken", repo=REPO, transport=transport, **kwargs), transport


class ReviewEventTests(unittest.TestCase):
    """T-ADV-13: the reviewer cannot approve, enforced at our boundary."""

    def test_approve_is_refused_and_sends_nothing(self) -> None:
        brk, transport = broker()
        with self.assertRaises(BrokerRefused):
            brk.post_review(pr=18, head_sha="abc123", body="looks good", event="APPROVE")
        self.assertEqual(transport.count, 0, "a refused approve must make no outbound request")

    def test_request_changes_is_refused_and_sends_nothing(self) -> None:
        brk, transport = broker()
        with self.assertRaises(BrokerRefused):
            brk.post_review(pr=18, head_sha="abc123", body="needs work", event="REQUEST_CHANGES")
        self.assertEqual(transport.count, 0)

    def test_every_non_comment_event_is_refused(self) -> None:
        for event in ("APPROVE", "REQUEST_CHANGES", "approve", "", "COMMENT ", "MERGE"):
            with self.subTest(event=event):
                brk, transport = broker()
                with self.assertRaises(BrokerRefused):
                    brk.post_review(pr=18, head_sha="abc", body="x", event=event)
                self.assertEqual(transport.count, 0, f"{event!r} reached the transport")

    def test_only_comment_is_permitted(self) -> None:
        self.assertEqual(REVIEWER_EVENTS, frozenset({"COMMENT"}))

    def test_the_refusal_names_the_event_and_the_rule(self) -> None:
        brk, _ = broker()
        with self.assertRaises(BrokerRefused) as caught:
            brk.post_review(pr=1, head_sha="a", body="b", event="APPROVE")
        message = str(caught.exception)
        self.assertIn("APPROVE", message)
        self.assertIn("COMMENT", message)
        self.assertIn("before any request", message)

    def test_a_refused_review_is_recorded(self) -> None:
        brk, _ = broker()
        with self.assertRaises(BrokerRefused):
            brk.post_review(pr=18, head_sha="abc", body="x", event="APPROVE")
        self.assertEqual(len(brk.receipts), 1)
        self.assertEqual(brk.receipts[0].result, "refused")
        self.assertIsNone(brk.receipts[0].status)


class CommentReviewTests(unittest.TestCase):
    def test_a_comment_review_sends_exactly_one_request(self) -> None:
        brk, transport = broker()
        brk.post_review(pr=18, head_sha="abc123", body="one finding")
        self.assertEqual(transport.count, 1)

    def test_the_payload_is_a_comment_event_at_the_given_head(self) -> None:
        brk, transport = broker()
        brk.post_review(pr=18, head_sha="abc123", body="one finding")
        url, payload, _headers = transport.requests[0]
        self.assertIn(f"/repos/{REPO}/pulls/18/reviews", url)
        self.assertEqual(payload["event"], "COMMENT")
        self.assertEqual(payload["commit_id"], "abc123")
        self.assertEqual(payload["body"], "one finding")

    def test_inline_comments_are_attached_with_path_and_line(self) -> None:
        brk, transport = broker()
        brk.post_review(
            pr=18,
            head_sha="abc",
            body="summary",
            comments=[{"path": "calc/__init__.py", "line": 6, "body": "this guard is wrong"}],
        )
        _url, payload, _ = transport.requests[0]
        self.assertEqual(payload["comments"][0]["path"], "calc/__init__.py")
        self.assertEqual(payload["comments"][0]["line"], 6)
        self.assertEqual(payload["comments"][0]["side"], "RIGHT")

    def test_an_empty_body_is_refused_before_sending(self) -> None:
        brk, transport = broker()
        for body in ("", "   ", "\n"):
            with self.subTest(body=body):
                with self.assertRaises(BrokerRefused):
                    brk.post_review(pr=18, head_sha="abc", body=body)
        self.assertEqual(transport.count, 0)

    def test_a_malformed_inline_comment_is_refused_before_sending(self) -> None:
        brk, transport = broker()
        for comment in (
            {"line": 6, "body": "b"},
            {"path": "calc/__init__.py", "body": "b"},
            {"path": "calc/__init__.py", "line": 0, "body": "b"},
            {"path": "calc/__init__.py", "line": 6},
        ):
            with self.subTest(comment=comment):
                with self.assertRaises(BrokerRefused):
                    brk.post_review(pr=18, head_sha="abc", body="x", comments=[comment])
        self.assertEqual(transport.count, 0)

    def test_the_authorization_header_is_sent(self) -> None:
        brk, transport = broker()
        brk.post_review(pr=1, head_sha="a", body="b")
        _url, _payload, headers = transport.requests[0]
        self.assertEqual(headers["Authorization"], "Bearer ghp_faketoken")

    def test_the_token_is_never_in_the_repr(self) -> None:
        brk, _ = broker()
        self.assertNotIn("ghp_faketoken", repr(brk))


class NoMergeTests(unittest.TestCase):
    """There must be no way to merge from this module at all."""

    def test_no_merge_method_exists(self) -> None:
        public = [n for n in dir(Broker) if not n.startswith("_")]
        for name in public:
            self.assertNotIn("merge", name.lower(), f"Broker.{name} looks like a merge path")

    def test_no_approve_helper_exists(self) -> None:
        public = [n for n in dir(Broker) if not n.startswith("_")]
        for name in public:
            self.assertNotIn("approve", name.lower())


class FailureTests(unittest.TestCase):
    def test_a_host_error_surfaces_with_its_status(self) -> None:
        transport = CountingTransport(error=BrokerError("HTTP 500", status=500))
        brk, _ = broker(transport=transport)
        with self.assertRaises(BrokerError) as caught:
            brk.post_review(pr=1, head_sha="a", body="b")
        self.assertEqual(caught.exception.status, 500)

    def test_receipts_are_written_to_the_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "broker.jsonl"
            brk, _ = broker(ledger_path=ledger)
            brk.post_review(pr=18, head_sha="abc", body="b")
            with self.assertRaises(BrokerRefused):
                brk.post_review(pr=18, head_sha="abc", body="b", event="APPROVE")

            lines = [json.loads(line) for line in ledger.read_text().splitlines()]
            self.assertEqual([entry["result"] for entry in lines], ["confirmed", "refused"])
            # the token is never written to the ledger
            self.assertNotIn("ghp_faketoken", ledger.read_text())


if __name__ == "__main__":
    unittest.main()
