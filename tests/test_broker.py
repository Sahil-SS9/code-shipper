"""Broker unit tests: the protocol refuses anything that is not a bounded model request,
and a broker that has failed refuses everything.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from code_shipper.inference.broker import (
    MAX_REQUEST_BYTES,
    Broker,
    StartupError,
    read_secret_file,
)
from code_shipper.inference.client import BrokerClient, InferenceUnavailable
from code_shipper.inference.upstream import FailingUpstream, OpenAICompatUpstream, StaticUpstream


def encode(**overrides: object) -> bytes:
    request = {
        "op": "complete",
        "capability": "cap",
        "provider": "local",
        "model": "m",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 16,
    }
    request.update(overrides)
    return json.dumps(request).encode()


def make_broker(**kwargs: object) -> tuple[Broker, str]:
    upstream = kwargs.pop("upstream", None) or StaticUpstream("the answer")
    broker = Broker(upstream, **kwargs)  # type: ignore[arg-type]
    cap = broker.issue_capability(run_id="run-1", provider="local", model="m")
    return broker, cap.token


class CompletePathTests(unittest.TestCase):
    def test_valid_request_returns_text(self) -> None:
        upstream = StaticUpstream("the answer", usage={"total_tokens": 5})
        broker = Broker(upstream)
        cap = broker.issue_capability(run_id="run-1", provider="local", model="m")

        response = broker.handle(encode(capability=cap.token))

        self.assertTrue(response["ok"])
        self.assertEqual(response["text"], "the answer")
        self.assertEqual(response["usage"]["completion_chars"], len("the answer"))
        self.assertEqual(upstream.calls[0]["model"], "m")

    def test_success_writes_a_receipt_without_prompt_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "receipts.jsonl"
            broker = Broker(StaticUpstream("ok"), ledger_path=ledger)
            cap = broker.issue_capability(run_id="run-1", provider="local", model="m")

            broker.handle(encode(capability=cap.token, messages=[{"role": "user", "content": "SECRET-PROMPT"}]))

            lines = ledger.read_text().splitlines()
            self.assertEqual(len(lines), 1)
            receipt = json.loads(lines[0])
            self.assertEqual(receipt["outcome"], "confirmed")
            self.assertEqual(receipt["run_id"], "run-1")
            self.assertEqual(len(receipt["prompt_sha256"]), 64)
            self.assertNotIn("SECRET-PROMPT", lines[0])

    def test_request_count_is_reserved_before_the_call(self) -> None:
        broker = Broker(StaticUpstream("ok"))
        cap = broker.issue_capability(run_id="run-1", provider="local", model="m", max_requests=2)

        self.assertTrue(broker.handle(encode(capability=cap.token))["ok"])
        self.assertTrue(broker.handle(encode(capability=cap.token))["ok"])
        third = broker.handle(encode(capability=cap.token))

        self.assertEqual(third, {"ok": False, "error": "capability_exhausted"})


class RefusesNonInferenceTests(unittest.TestCase):
    """The broker serves model completions and nothing else."""

    def setUp(self) -> None:
        self.broker, self.token = make_broker()

    def assertRefused(self, raw: bytes, code: str) -> None:
        response = self.broker.handle(raw)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"], code)
        self.assertNotIn("text", response)

    def test_refuses_shell_operation(self) -> None:
        self.assertRefused(encode(op="shell", capability=self.token), "unsupported_operation")

    def test_refuses_file_read_operation(self) -> None:
        self.assertRefused(encode(op="read_file", path="/home/kensei/.hermes/.env"), "unsupported_operation")

    def test_refuses_http_proxy_operation(self) -> None:
        self.assertRefused(
            encode(op="http", url="https://api.github.com/user"), "unsupported_operation"
        )

    def test_refuses_unknown_field_alongside_a_valid_request(self) -> None:
        self.assertRefused(encode(argv=["sh", "-c", "id"]), "unknown_field")

    def test_refuses_extra_path_on_a_message(self) -> None:
        messages = [{"role": "user", "content": "hi", "path": "/etc/passwd"}]
        self.assertRefused(encode(messages=messages), "bad_request")

    def test_refuses_an_unknown_role(self) -> None:
        messages = [{"role": "wizard", "content": "hi"}]
        self.assertRefused(encode(messages=messages), "bad_request")

    def test_accepts_the_tool_role(self) -> None:
        # A tool result has to come back to the model, or an agent cannot act on what it read.
        broker = Broker(StaticUpstream("ok"))
        cap = broker.issue_capability(run_id="r", provider="local", model="m")
        response = broker.handle(
            encode(
                capability=cap.token,
                messages=[
                    {"role": "user", "content": "hi"},
                    {"role": "tool", "content": "file contents"},
                ],
            )
        )
        self.assertTrue(response["ok"], response)

    def test_refuses_missing_capability(self) -> None:
        self.assertRefused(encode(capability=""), "capability_required")

    def test_refuses_unknown_capability(self) -> None:
        self.assertRefused(encode(capability="not-a-token"), "capability_unknown")

    def test_refuses_revoked_capability(self) -> None:
        self.broker.revoke(self.token)
        self.assertRefused(encode(capability=self.token), "capability_revoked")

    def test_refuses_expired_capability(self) -> None:
        broker = Broker(StaticUpstream("ok"), clock=lambda: 1000.0)
        cap = broker.issue_capability(run_id="r", provider="local", model="m", ttl_seconds=60)
        broker._clock = lambda: 1061.0  # noqa: SLF001 - deliberate clock advance
        self.assertEqual(broker.handle(encode(capability=cap.token))["error"], "capability_expired")

    def test_refuses_model_outside_the_capability(self) -> None:
        broker = Broker(StaticUpstream("ok"))
        cap = broker.issue_capability(run_id="r", provider="local", model="m")
        response = broker.handle(encode(capability=cap.token, model="gpt-expensive"))
        self.assertEqual(response["error"], "model_not_allowed")

    def test_refuses_provider_outside_the_capability(self) -> None:
        broker = Broker(StaticUpstream("ok"))
        cap = broker.issue_capability(run_id="r", provider="local", model="m")
        response = broker.handle(encode(capability=cap.token, provider="anthropic"))
        self.assertEqual(response["error"], "model_not_allowed")

    def test_refuses_output_tokens_above_the_capability(self) -> None:
        broker = Broker(StaticUpstream("ok"))
        cap = broker.issue_capability(run_id="r", provider="local", model="m", max_output_tokens=32)
        response = broker.handle(encode(capability=cap.token, max_tokens=64))
        self.assertEqual(response["error"], "token_limit_exceeded")

    def test_refuses_prompt_above_the_capability(self) -> None:
        broker = Broker(StaticUpstream("ok"))
        cap = broker.issue_capability(run_id="r", provider="local", model="m", max_prompt_chars=10)
        response = broker.handle(encode(capability=cap.token, messages=[{"role": "user", "content": "x" * 11}]))
        self.assertEqual(response["error"], "prompt_too_large")

    def test_refuses_malformed_json(self) -> None:
        self.assertRefused(b"{not json", "malformed_json")

    def test_refuses_a_json_array(self) -> None:
        self.assertRefused(b"[1, 2, 3]", "bad_request")

    def test_refuses_oversized_request(self) -> None:
        self.assertRefused(b"x" * (MAX_REQUEST_BYTES + 1), "request_too_large")

    def test_refuses_bad_max_tokens(self) -> None:
        self.assertRefused(encode(max_tokens=0), "bad_request")
        self.assertRefused(encode(max_tokens="many"), "bad_request")


class FailsClosedTests(unittest.TestCase):
    """If the broker is not healthy, no request succeeds."""

    def test_broker_without_upstream_refuses_everything(self) -> None:
        broker = Broker(None)
        cap = broker.issue_capability(run_id="r", provider="local", model="m")
        response = broker.handle(encode(capability=cap.token))
        self.assertEqual(response, {"ok": False, "error": "broker_failed"})

    def test_failed_broker_refuses_even_a_valid_request(self) -> None:
        broker, token = make_broker()
        broker.fail("upstream_contract_broken")
        self.assertEqual(broker.handle(encode(capability=token))["error"], "broker_failed")

    def test_upstream_exception_is_a_refusal_not_a_fallback(self) -> None:
        broker = Broker(FailingUpstream("connection refused"))
        cap = broker.issue_capability(run_id="r", provider="local", model="m")
        response = broker.handle(encode(capability=cap.token))
        self.assertEqual(response, {"ok": False, "error": "upstream_error"})

    def test_empty_completion_is_a_refusal(self) -> None:
        broker = Broker(StaticUpstream("   "))
        cap = broker.issue_capability(run_id="r", provider="local", model="m")
        self.assertEqual(broker.handle(encode(capability=cap.token))["error"], "upstream_error")

    def test_unwritable_ledger_refuses_to_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            blocked = Path(tmp) / "file"
            blocked.write_text("x")
            with self.assertRaises(StartupError):
                Broker(StaticUpstream("ok"), ledger_path=blocked / "receipts.jsonl")

    def test_ledger_write_failure_stops_the_broker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "receipts.jsonl"
            broker = Broker(StaticUpstream("ok"), ledger_path=ledger)
            cap = broker.issue_capability(run_id="r", provider="local", model="m", max_requests=5)
            self.assertTrue(broker.handle(encode(capability=cap.token))["ok"])

            # Append to an existing file survives a read-only directory, so make the file itself
            # unwritable: that is the state a full disk or a revoked mount produces.
            os.chmod(ledger, 0o400)
            try:
                refused = broker.handle(encode(capability=cap.token))
            finally:
                os.chmod(ledger, 0o600)

            self.assertEqual(broker.failed, "ledger_write_failed")
            self.assertEqual(refused, {"ok": False, "error": "receipt_write_failed"})
            self.assertEqual(broker.handle(encode(capability=cap.token))["error"], "broker_failed")

    def test_upstream_error_writes_an_incident_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "receipts.jsonl"
            broker = Broker(FailingUpstream(), ledger_path=ledger)
            cap = broker.issue_capability(run_id="r", provider="local", model="m")
            broker.handle(encode(capability=cap.token))
            receipt = json.loads(ledger.read_text().splitlines()[0])
            self.assertEqual(receipt["outcome"], "refused")
            self.assertEqual(receipt["error"], "upstream_error")


class CapabilityTests(unittest.TestCase):
    def test_capability_limits_are_validated(self) -> None:
        broker = Broker(StaticUpstream("ok"))
        with self.assertRaises(ValueError):
            broker.issue_capability(run_id="r", provider="p", model="m", max_requests=0)
        with self.assertRaises(ValueError):
            broker.issue_capability(run_id="r", provider="p", model="m", max_output_tokens=10**6)

    def test_public_view_never_contains_the_token(self) -> None:
        broker = Broker(StaticUpstream("ok"))
        cap = broker.issue_capability(run_id="r", provider="p", model="m")
        self.assertNotIn(cap.token, json.dumps(cap.public()))
        self.assertEqual(cap.public()["requests_used"], 0)

    def test_tokens_are_unique(self) -> None:
        broker = Broker(StaticUpstream("ok"))
        tokens = {broker.issue_capability(run_id=f"r{i}", provider="p", model="m").token for i in range(20)}
        self.assertEqual(len(tokens), 20)


class SecretHandlingTests(unittest.TestCase):
    def test_upstream_repr_redacts_the_key(self) -> None:
        upstream = OpenAICompatUpstream("http://127.0.0.1:1", "sk-real-secret-value")
        self.assertNotIn("sk-real-secret-value", repr(upstream))

    def test_read_secret_file_raises_on_missing_or_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(StartupError):
                read_secret_file(Path(tmp) / "nope")
            empty = Path(tmp) / "empty"
            empty.write_text("\n")
            with self.assertRaises(StartupError):
                read_secret_file(empty)

    def test_read_secret_file_strips_trailing_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "key"
            path.write_text("sk-fake\n")
            self.assertEqual(read_secret_file(path), "sk-fake")


class ClientTests(unittest.TestCase):
    def test_client_reports_unreachable_broker_as_unavailable(self) -> None:
        client = BrokerClient(
            socket_path="/nonexistent/broker.sock",
            capability="cap",
            provider="local",
            model="m",
        )
        with self.assertRaises(InferenceUnavailable):
            client.complete("hello")

    def test_client_requires_full_configuration(self) -> None:
        with self.assertRaises(InferenceUnavailable):
            BrokerClient.from_env({})

    def test_client_round_trip_against_a_real_socket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            broker = Broker(StaticUpstream("real text"))
            cap = broker.issue_capability(run_id="r", provider="local", model="m")
            server = broker.serve_socket(Path(tmp) / "b.sock").start()
            try:
                client = BrokerClient(
                    socket_path=str(Path(tmp) / "b.sock"),
                    capability=cap.token,
                    provider="local",
                    model="m",
                )
                self.assertEqual(client.complete("hi"), "real text")
            finally:
                server.stop()

    def test_client_gets_no_text_when_the_broker_is_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            socket_path = Path(tmp) / "b.sock"
            broker = Broker(StaticUpstream("real text"))
            cap = broker.issue_capability(run_id="r", provider="local", model="m")
            server = broker.serve_socket(socket_path).start()
            server.stop()

            client = BrokerClient(
                socket_path=str(socket_path), capability=cap.token, provider="local", model="m"
            )
            with self.assertRaises(InferenceUnavailable):
                client.complete("hi")


if __name__ == "__main__":
    unittest.main()
