"""A loopback OpenAI-compatible adapter that runs INSIDE the container.

Hermes speaks the OpenAI chat-completions wire format. The broker speaks one JSON line over a unix
socket. This process is the translation: it listens on 127.0.0.1:PORT inside the container's own
network namespace, accepts a /v1/chat/completions POST, and forwards it to the broker socket.

It holds no credential. The `Authorization` header Hermes sends is a placeholder that this process
discards — the real key lives only in the broker, on the host.

Why a separate process rather than a monkeypatch: the worker must be provably unable to reach a
provider directly. This listens on loopback inside a `--network none` namespace, so the only thing
it can reach is the mounted broker socket, and the only thing that can reach it is the container.

Wire behaviour that matters:
  * only /v1/chat/completions (and /v1/models) are served; anything else is 404
  * only POST is accepted for completions
  * a broker refusal becomes a 502 with an OpenAI-shaped error body, never a fabricated completion
  * the response is shaped as Hermes expects, including usage, so it does not fall back
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORK = Path("/work")
BROKER_FILE = WORK / "broker.json"
CAPABILITY_FILE = WORK / "capability"

MAX_BODY = 2 * 1024 * 1024
call_count = 0
count_lock = threading.Lock()


class BrokerRefused(Exception):
    """The broker said no. Distinct from a transport failure only for the log message."""


def load_broker_config() -> dict:
    config = json.loads(BROKER_FILE.read_text(encoding="utf-8"))
    for key in ("socket", "provider", "model"):
        if not config.get(key):
            raise SystemExit(f"adapter: broker.json missing {key}")
    return config


def call_broker(config: dict, payload: dict) -> dict:
    """One request, one response, over the unix socket. Raises on any failure."""
    capability = CAPABILITY_FILE.read_text(encoding="utf-8").strip()
    request = {
        "op": "complete",
        "capability": capability,
        "provider": config["provider"],
        "model": config["model"],
        "messages": payload["messages"],
        "max_tokens": payload.get("max_completion_tokens") or payload.get("max_tokens") or 512,
    }
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(config.get("timeout", 180))
    try:
        sock.connect(config["socket"])
    except OSError as exc:
        sock.close()
        raise BrokerRefused(f"broker unreachable: {exc.strerror or exc}") from None
    try:
        sock.sendall(json.dumps(request).encode("utf-8") + b"\n")
        raw = b""
        while b"\n" not in raw:
            chunk = sock.recv(65536)
            if not chunk:
                break
            raw += chunk
    finally:
        sock.close()
    if not raw:
        raise BrokerRefused("broker closed the connection without a response")
    response = json.loads(raw.split(b"\n", 1)[0])
    if not response.get("ok"):
        raise BrokerRefused(f"broker refused: {response.get('error')}")
    return response


def _count() -> int:
    global call_count
    with count_lock:
        call_count += 1
        return call_count


def make_handler(config: dict):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            # Hermes probes /v1/models when the provider is configured as custom.
            if self.path.rstrip("/") in ("/v1/models", "/models"):
                self._json(
                    200,
                    {
                        "object": "list",
                        "data": [
                            {
                                "id": config["model"],
                                "object": "model",
                                "owned_by": "code-shipper-broker",
                            }
                        ],
                    },
                )
                return
            self._json(404, {"error": {"message": "not found", "type": "invalid_request_error"}})

        def do_POST(self) -> None:  # noqa: N802
            if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
                self._json(404, {"error": {"message": "not found", "type": "invalid_request_error"}})
                return
            try:
                payload = self._read_body()
            except ValueError as exc:
                self._json(400, {"error": {"message": str(exc), "type": "invalid_request_error"}})
                return
            if not isinstance(payload.get("messages"), list) or not payload["messages"]:
                self._json(
                    400, {"error": {"message": "messages required", "type": "invalid_request_error"}}
                )
                return

            try:
                response = call_broker(config, payload)
            except BrokerRefused as exc:
                # Fail closed. A 502 makes Hermes report an error; it must not look like a completion.
                print(f"adapter: {exc}", file=sys.stderr, flush=True)
                self._json(502, {"error": {"message": str(exc), "type": "upstream_error"}})
                return
            except (OSError, json.JSONDecodeError) as exc:
                print(f"adapter: transport failure: {exc}", file=sys.stderr, flush=True)
                self._json(502, {"error": {"message": "broker transport failure", "type": "upstream_error"}})
                return

            n = _count()
            usage = response.get("usage") or {}
            completion = {
                "id": f"chatcmpl-broker-{n}",
                "object": "chat.completion",
                "model": config["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": response["text"]},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": usage.get("prompt_chars", 0),
                    "completion_tokens": usage.get("completion_chars", 0),
                    "total_tokens": usage.get("prompt_chars", 0) + usage.get("completion_chars", 0),
                },
            }
            self._json(200, completion)

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                raise ValueError("empty body")
            if length > MAX_BODY:
                raise ValueError("body too large")
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                raise ValueError("malformed json") from None
            if not isinstance(payload, dict):
                raise ValueError("body must be an object")
            return payload

        def _json(self, status: int, body: dict) -> None:
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except OSError:
                pass

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            # Never log headers: the placeholder key is harmless but the habit is not.
            print(f"adapter: {format % args}", file=sys.stderr, flush=True)

    return Handler


def main(argv: list[str]) -> int:
    port = int(argv[1]) if len(argv) > 1 else 8111
    config = load_broker_config()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(config))
    server.daemon_threads = True
    print(f"adapter: listening on 127.0.0.1:{port} -> {config['socket']}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
