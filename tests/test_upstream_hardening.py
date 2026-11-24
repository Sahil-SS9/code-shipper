"""Regression tests for two CP1 review findings.

1. A redirecting provider must not receive the broker's credential. urllib follows 3xx by
   default and resends ``Authorization`` to the new host, so the upstream client refuses every
   redirect instead.
2. ``Broker.serve_socket`` must default to an owner-only socket, so widening it for a worker in
   another UID is a visible choice at the call site.

Both tests use loopback servers on ephemeral ports and a fake key. Nothing here reaches the
network or reads a real credential.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from code_shipper.inference.broker import Broker
from code_shipper.inference.upstream import FailingUpstream, OpenAICompatUpstream, StaticUpstream

FAKE_KEY = "sk-FAKE-regression-test-key"


class _Recorder:
    """A loopback HTTP server that records what it was sent."""

    def __init__(self, handler_factory) -> None:
        self.hits: list[dict[str, str | None]] = []
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                recorder.hits.append(
                    {
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "host": self.headers.get("Host"),
                    }
                )
                handler_factory(self, recorder)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> "_Recorder":
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _answer(handler: BaseHTTPRequestHandler, _recorder: _Recorder) -> None:
    body = json.dumps(
        {"choices": [{"message": {"content": "pong"}}], "usage": {"total_tokens": 3}}
    ).encode()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class RedirectLeakTests(unittest.TestCase):
    """Finding 1: a 3xx must not forward the bearer token anywhere."""

    def test_redirect_is_refused_and_target_gets_no_authorization(self) -> None:
        redirect_target: _Recorder

        def redirect(handler: BaseHTTPRequestHandler, _recorder: _Recorder) -> None:
            handler.send_response(302)
            handler.send_header("Location", f"{redirect_target.url}/v1/chat/completions")
            handler.send_header("Content-Length", "0")
            handler.end_headers()

        with _Recorder(_answer) as redirect_target:
            with _Recorder(redirect) as origin:
                upstream = OpenAICompatUpstream(origin.url, FAKE_KEY)

                with self.assertRaises(Exception) as caught:
                    upstream.complete(
                        provider="fake",
                        model="m",
                        messages=[{"role": "user", "content": "hi"}],
                        max_tokens=8,
                        timeout=10,
                    )

                self.assertIn("redirect refused", str(caught.exception).lower())
                # the whole point: the key never arrives at the redirect host
                self.assertEqual(redirect_target.hits, [], "redirect target was contacted")

    def test_broker_returns_upstream_error_on_redirect(self) -> None:
        target: _Recorder

        def redirect(handler: BaseHTTPRequestHandler, _recorder: _Recorder) -> None:
            handler.send_response(307)
            handler.send_header("Location", f"{target.url}/v1/chat/completions")
            handler.send_header("Content-Length", "0")
            handler.end_headers()

        with _Recorder(_answer) as target:
            with _Recorder(redirect) as origin:
                broker = Broker(OpenAICompatUpstream(origin.url, FAKE_KEY))
                cap = broker.issue_capability(run_id="r", provider="fake", model="m")
                response = broker.handle(
                    json.dumps(
                        {
                            "op": "complete",
                            "capability": cap.token,
                            "provider": "fake",
                            "model": "m",
                            "messages": [{"role": "user", "content": "hi"}],
                            "max_tokens": 8,
                        }
                    ).encode()
                )

                self.assertEqual(response, {"ok": False, "error": "upstream_error"})
                self.assertEqual(target.hits, [], "redirect target was contacted")

    def test_every_3xx_class_is_refused(self) -> None:
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                target_holder: list[_Recorder] = []

                def redirect(handler: BaseHTTPRequestHandler, _recorder: _Recorder, code: int = code) -> None:
                    handler.send_response(code)
                    handler.send_header("Location", f"{target_holder[0].url}/v1/chat/completions")
                    handler.send_header("Content-Length", "0")
                    handler.end_headers()

                with _Recorder(_answer) as target:
                    target_holder.append(target)
                    with _Recorder(redirect) as origin:
                        upstream = OpenAICompatUpstream(origin.url, FAKE_KEY)
                        with self.assertRaises(Exception):
                            upstream.complete(
                                provider="fake",
                                model="m",
                                messages=[{"role": "user", "content": "hi"}],
                                max_tokens=8,
                                timeout=10,
                            )
                        self.assertEqual(target.hits, [], f"{code} followed to the target host")

    def test_a_normal_response_still_works(self) -> None:
        with _Recorder(_answer) as upstream_server:
            upstream = OpenAICompatUpstream(upstream_server.url, FAKE_KEY)
            completion = upstream.complete(
                provider="fake",
                model="m",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=8,
                timeout=10,
            )
            self.assertEqual(completion.text, "pong")
            self.assertEqual(upstream_server.hits[0]["authorization"], f"Bearer {FAKE_KEY}")


class SocketModeDefaultTests(unittest.TestCase):
    """Finding 2: the library default must be owner-only."""

    def test_serve_socket_defaults_to_0600(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            broker = Broker(StaticUpstream("ok"))
            server = broker.serve_socket(Path(tmp) / "b.sock").start()
            try:
                mode = os.stat(Path(tmp) / "b.sock").st_mode & 0o777
                self.assertEqual(mode, 0o600, "library default must be owner-only")
            finally:
                server.stop()

    def test_widening_the_socket_is_explicit_and_effective(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            broker = Broker(StaticUpstream("ok"))
            server = broker.serve_socket(Path(tmp) / "b.sock", mode=0o660).start()
            try:
                mode = os.stat(Path(tmp) / "b.sock").st_mode & 0o777
                self.assertEqual(mode, 0o660)
            finally:
                server.stop()


class UnchangedFailurePathsTests(unittest.TestCase):
    """Guard against the redirect fix swallowing real upstream failures."""

    def test_transport_error_still_maps_to_upstream_error(self) -> None:
        broker = Broker(FailingUpstream())
        cap = broker.issue_capability(run_id="r", provider="fake", model="m")
        response = broker.handle(
            json.dumps(
                {
                    "op": "complete",
                    "capability": cap.token,
                    "provider": "fake",
                    "model": "m",
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 8,
                }
            ).encode()
        )
        self.assertEqual(response["error"], "upstream_error")


if __name__ == "__main__":
    unittest.main()
