"""Tests for the in-container loopback adapter.

The adapter is the only thing between Hermes and the broker. Its important properties are negative:
it holds no credential, it serves only the OpenAI shapes Hermes uses, and a broker refusal becomes a
502 rather than a fabricated completion. A fabricated completion here would be the worst possible
failure, because it would look like a successful model call.

The broker is simulated with a real unix socket so the wire path is exercised, not mocked.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import sys
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from code_shipper.contain import adapter
# Unix-domain sockets and POSIX file modes do not exist on Windows. These tests are
# skipped there rather than allowed to fail, so the CI matrix reports real coverage.
posix_only = unittest.skipIf(sys.platform.startswith("win"), "POSIX-only test")



class FakeBroker:
    """A real unix-socket server that answers like the broker, with a scripted response."""

    def __init__(self, path: Path, *, ok: bool = True, error: str = "capability_exhausted") -> None:
        self.path = path
        self.ok = ok
        self.error = error
        self.requests: list[dict] = []
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(path))
        self._sock.listen(8)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> "FakeBroker":
        self._thread.start()
        return self

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            try:
                raw = b""
                while b"\n" not in raw:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    raw += chunk
                request = json.loads(raw.split(b"\n", 1)[0])
                self.requests.append(request)
                if self.ok:
                    response = {
                        "ok": True,
                        "text": "pong-from-broker",
                        "usage": {"prompt_chars": 12, "completion_chars": 16},
                    }
                else:
                    response = {"ok": False, "error": self.error}
                conn.sendall(json.dumps(response).encode() + b"\n")
            except (OSError, json.JSONDecodeError):
                pass
            finally:
                conn.close()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass


@posix_only
class AdapterCase(unittest.TestCase):
    broker_ok = True

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="cs-adapter-"))
        self.addCleanup(self._cleanup)

        self.broker = FakeBroker(self.tmp / "broker.sock", ok=self.broker_ok).start()
        self.addCleanup(self.broker.stop)

        config = {
            "socket": str(self.tmp / "broker.sock"),
            "provider": "local",
            "model": "auto",
            "timeout": 10,
        }
        (self.tmp / "broker.json").write_text(json.dumps(config))
        (self.tmp / "capability").write_text("CAP-FAKE\n")

        # point the module at the temp dir instead of the real /work
        self._saved_work = adapter.WORK
        self._saved_broker = adapter.BROKER_FILE
        self._saved_cap = adapter.CAPABILITY_FILE
        adapter.WORK = self.tmp
        adapter.BROKER_FILE = self.tmp / "broker.json"
        adapter.CAPABILITY_FILE = self.tmp / "capability"

        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), adapter.make_handler(adapter.load_broker_config())
        )
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _cleanup(self) -> None:
        adapter.WORK = self._saved_work
        adapter.BROKER_FILE = self._saved_broker
        adapter.CAPABILITY_FILE = self._saved_cap
        self.server.shutdown()
        self.server.server_close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def post(self, path: str, body: dict, headers: dict | None = None) -> tuple[int, dict]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def get(self, path: str) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def post_stream(self, body: dict) -> list[dict]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        events: list[dict] = []
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertTrue(
                response.headers["Content-Type"].startswith("text/event-stream"),
                "streaming request must be answered as SSE",
            )
            for raw in response.read().decode().splitlines():
                if not raw.startswith("data: "):
                    continue
                payload = raw[len("data: "):].strip()
                if not payload:
                    continue  # the terminal "data:" sentinel
                events.append(json.loads(payload))
        return events


class ServingTests(AdapterCase):
    def test_completion_is_forwarded_and_shaped_like_openai(self) -> None:
        status, body = self.post(
            "/v1/chat/completions",
            {"model": "auto", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["object"], "chat.completion")
        self.assertEqual(body["choices"][0]["message"]["content"], "pong-from-broker")
        self.assertEqual(body["choices"][0]["message"]["role"], "assistant")
        self.assertIn("usage", body)
        self.assertEqual(body["model"], "auto")

    def test_the_placeholder_authorization_header_is_not_forwarded(self) -> None:
        self.post(
            "/v1/chat/completions",
            {"messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer DUMMY-PLACEHOLDER-KEY"},
        )
        self.assertEqual(len(self.broker.requests), 1)
        forwarded = json.dumps(self.broker.requests[0])
        self.assertNotIn("DUMMY-PLACEHOLDER-KEY", forwarded)
        self.assertNotIn("Authorization", forwarded)

    def test_the_broker_receives_the_capability_and_pinned_model(self) -> None:
        self.post("/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]})
        sent = self.broker.requests[0]
        self.assertEqual(sent["capability"], "CAP-FAKE")
        self.assertEqual(sent["model"], "auto")
        self.assertEqual(sent["provider"], "local")
        self.assertEqual(sent["op"], "complete")

    def test_max_completion_tokens_is_honoured(self) -> None:
        self.post(
            "/v1/chat/completions",
            {"messages": [{"role": "user", "content": "hi"}], "max_completion_tokens": 33},
        )
        self.assertEqual(self.broker.requests[0]["max_tokens"], 33)

    def test_models_endpoint_lists_the_pinned_model(self) -> None:
        status, body = self.get("/v1/models")
        self.assertEqual(status, 200)
        self.assertEqual(body["data"][0]["id"], "auto")

    def test_unknown_path_is_404(self) -> None:
        status, _ = self.post("/v1/completions", {"messages": []})
        self.assertEqual(status, 404)
        self.assertEqual(self.broker.requests, [])

    def test_missing_messages_is_400(self) -> None:
        status, body = self.post("/v1/chat/completions", {"model": "auto"})
        self.assertEqual(status, 400)
        self.assertIn("messages required", body["error"]["message"])
        self.assertEqual(self.broker.requests, [])

    def test_malformed_body_is_400(self) -> None:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/chat/completions",
            data=b"{not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=10)
            self.fail("expected an error")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)


class RefusalTests(AdapterCase):
    """A broker refusal must never look like a completion."""

    broker_ok = False

    def test_broker_refusal_becomes_502(self) -> None:
        status, body = self.post(
            "/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]}
        )
        self.assertEqual(status, 502)
        self.assertIn("capability_exhausted", body["error"]["message"])
        self.assertNotIn("choices", body)

    def test_no_completion_text_is_returned_on_refusal(self) -> None:
        _, body = self.post("/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]})
        self.assertNotIn("choices", body)
        self.assertNotIn("pong", json.dumps(body))


class DeadBrokerTests(AdapterCase):
    def test_unreachable_broker_is_502_not_an_empty_completion(self) -> None:
        self.broker.stop()
        os.unlink(self.tmp / "broker.sock")
        status, body = self.post(
            "/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]}
        )
        self.assertEqual(status, 502)
        self.assertIn("broker", body["error"]["message"].lower())
        self.assertNotIn("choices", body)


class StreamingTests(AdapterCase):
    """Hermes uses the OpenAI client with stream=True. Plain JSON there breaks it."""

    def test_streaming_request_returns_sse_chunks(self) -> None:
        events = self.post_stream(
            {"messages": [{"role": "user", "content": "hi"}], "stream": True}
        )
        self.assertGreaterEqual(len(events), 2)
        self.assertTrue(all(e["object"] == "chat.completion.chunk" for e in events))

        text = "".join(
            event["choices"][0]["delta"].get("content") or "" for event in events
        )
        self.assertEqual(text, "pong-from-broker")

    def test_streaming_ends_with_a_stop_finish_reason(self) -> None:
        events = self.post_stream(
            {"messages": [{"role": "user", "content": "hi"}], "stream": True}
        )
        self.assertEqual(events[-1]["choices"][0]["finish_reason"], "stop")

    def test_non_streaming_request_still_returns_json(self) -> None:
        status, body = self.post(
            "/v1/chat/completions",
            {"messages": [{"role": "user", "content": "hi"}], "stream": False},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["object"], "chat.completion")


class CallCountingTests(AdapterCase):
    """The counters are evidence, so they must actually count."""

    def read_calls(self) -> int:
        return int(json.loads((self.tmp / "adapter-calls.json").read_text())["calls"])

    def read_refused(self) -> int:
        return int(json.loads((self.tmp / "adapter-refused.json").read_text())["refused"])

    def test_no_calls_file_before_any_request(self) -> None:
        self.assertFalse((self.tmp / "adapter-calls.json").exists())
        self.assertFalse((self.tmp / "adapter-refused.json").exists())

    def test_each_forwarded_request_increments_the_count(self) -> None:
        for _ in range(3):
            self.post("/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(self.read_calls(), 3)

    def test_refused_requests_do_not_increment_the_count(self) -> None:
        # a 400 is rejected before the broker, so it is not a model call
        self.post("/v1/chat/completions", {"model": "auto"})
        self.assertFalse((self.tmp / "adapter-calls.json").exists())

    def test_streaming_requests_are_counted(self) -> None:
        self.post_stream({"messages": [{"role": "user", "content": "hi"}], "stream": True})
        self.assertEqual(self.read_calls(), 1)


class RefusalCountingTests(AdapterCase):
    """A refusal must leave evidence that an attempt was made.

    Without this, a fail-closed run cannot be told apart from a run that never reached the adapter,
    which is the difference between proving the boundary and assuming it.
    """

    broker_ok = False

    def read_refused(self) -> int:
        return int(json.loads((self.tmp / "adapter-refused.json").read_text())["refused"])

    def test_refusal_increments_the_refused_counter(self) -> None:
        self.post("/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(self.read_refused(), 1)

    def test_refusal_does_not_increment_the_completion_counter(self) -> None:
        self.post("/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]})
        self.assertFalse((self.tmp / "adapter-calls.json").exists())

    def test_repeated_refusals_all_count(self) -> None:
        for _ in range(3):
            self.post("/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(self.read_refused(), 3)

    def test_streaming_refusal_is_also_counted(self) -> None:
        self.post("/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}], "stream": True})
        self.assertEqual(self.read_refused(), 1)


class DeadBrokerRefusalTests(AdapterCase):
    def test_unreachable_broker_increments_refused(self) -> None:
        self.broker.stop()
        os.unlink(self.tmp / "broker.sock")
        self.post("/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]})
        refused = json.loads((self.tmp / "adapter-refused.json").read_text())["refused"]
        self.assertEqual(refused, 1)


@posix_only
class CredentialFreeTests(unittest.TestCase):
    def test_adapter_source_contains_no_credential_or_provider_url(self) -> None:
        source = Path(adapter.__file__).read_text(encoding="utf-8")
        for forbidden in ("sk-", "api.openai.com", "anthropic.com", "8091"):
            self.assertNotIn(forbidden, source, f"adapter must not reference {forbidden}")


if __name__ == "__main__":
    unittest.main()
