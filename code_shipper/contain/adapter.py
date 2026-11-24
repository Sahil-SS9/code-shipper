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
import signal
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORK = Path("/work")
BROKER_FILE = WORK / "broker.json"
CAPABILITY_FILE = WORK / "capability"

MAX_BODY = 2 * 1024 * 1024


def _calls_file() -> Path:
    # Derived from WORK at call time, not bound at import: the tests point WORK at a temp dir, and
    # a module-level constant would keep writing to the real /work.
    return WORK / "adapter-calls.json"


def _count() -> int:
    """Increment and return the on-disk call count, under a lock file.

    A file-based counter, not a module global: the worker starts the adapter as a subprocess, so an
    in-process counter is invisible across that boundary. Counting on disk also survives a crash,
    which is what makes the number usable as evidence.
    """
    lock = WORK / "adapter-calls.lock"
    try:
        with open(lock, "a+") as handle:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass
            try:
                current = int(json.loads(_calls_file().read_text(encoding="utf-8"))["calls"])
            except (OSError, KeyError, ValueError, json.JSONDecodeError):
                current = 0
            current += 1
            _calls_file().write_text(json.dumps({"calls": current}), encoding="utf-8")
            return current
    except OSError:
        return -1


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
            prompt_tokens = usage.get("prompt_chars", 0)
            completion_tokens = usage.get("completion_chars", 0)

            if payload.get("stream"):
                # Hermes uses the OpenAI client with stream=True by default. Answering a streaming
                # request with a plain JSON body makes the client fail to parse the SSE stream, so
                # the adapter must speak SSE whenever the caller asks for it.
                self._sse(
                    response["text"],
                    model=config["model"],
                    completion_id=f"chatcmpl-broker-{n}",
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
                return

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
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            }
            self._json(200, completion)

        def _sse(
            self,
            text: str,
            *,
            model: str,
            completion_id: str,
            prompt_tokens: int,
            completion_tokens: int,
        ) -> None:
            """Emit the text as an OpenAI chat.completion.chunk stream, then [DONE]."""
            def chunk(delta: dict, finish: str | None) -> dict:
                return {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "model": model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }

            events = [
                chunk({"role": "assistant", "content": ""}, None),
                chunk({"content": text}, None),
                chunk({}, "stop"),
            ]
            body = b"".join(
                f"data: {json.dumps(event)}\n\n".encode() for event in events
            ) + b"data:"

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
                self.wfile.flush()
            except OSError:
                pass

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

    def _stop(_signum: int, _frame: object) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        # The call count is already on disk, incremented per request; nothing to flush here.
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
