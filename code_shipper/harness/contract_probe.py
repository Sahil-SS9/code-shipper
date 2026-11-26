"""A real one-shot contract probe for the hermes adapter.

`hermes --version` proves the binary exists. It does not prove the flags this adapter passes are
still valid, that a prompt on stdin works, or that the output can be parsed. This module runs an
actual one-shot against a loopback fake provider in a throwaway HERMES_HOME, so the contract is
measured rather than assumed.

The real `~/.hermes` is never touched: HERMES_HOME and HOME both point at a temp directory, and the
probe refuses to run if it would resolve inside the user's real Hermes directory.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class _FakeProvider:
    """A loopback OpenAI-compatible endpoint that answers with a fixed JSON object."""

    def __init__(self, content: str, *, stream: bool = True) -> None:
        self.content = content
        self.stream = stream
        self.requests: list[dict[str, Any]] = []

    def __enter__(self) -> "_FakeProvider":
        probe = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                probe.requests.append(body)
                if body.get("stream"):
                    self._sse(probe.content)
                else:
                    self._json(probe.content)

            def do_GET(self) -> None:  # noqa: N802
                raw = b'{"object":"list","data":[{"id":"probe-model"}]}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _json(self, content: str) -> None:
                raw = json.dumps(
                    {
                        "choices": [
                            {"message": {"role": "assistant", "content": content},
                             "finish_reason": "stop"}
                        ],
                        "usage": {"total_tokens": 5},
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _sse(self, content: str) -> None:
                def chunk(delta: dict, finish: str | None) -> dict:
                    return {
                        "id": "probe",
                        "object": "chat.completion.chunk",
                        "model": "probe-model",
                        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                    }

                events = [
                    chunk({"role": "assistant", "content": ""}, None),
                    chunk({"content": content}, None),
                    chunk({}, "stop"),
                ]
                body = b"".join(
                    f"data: {json.dumps(e)}\n\n".encode() for e in events
                ) + b"data:"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"


def _assert_isolated(hermes_home: Path) -> None:
    resolved = hermes_home.resolve()
    real = (Path.home() / ".hermes").resolve()
    if resolved == real or real in resolved.parents:
        raise RuntimeError(f"refusing to run a probe inside the real Hermes home: {resolved}")


@contextlib.contextmanager
def _probe_dir():
    """A probe directory that can be cleaned up after the container has written into it.

    `TemporaryDirectory` raises from its own `__exit__` if the tree cannot be removed, which turned a
    passing probe into a test *error*: the worker runs as uid 65534 inside the container and leaves files
    the host user cannot unlink, so `shutil.rmtree` reaches a parent whose child it could not remove and
    fails with "Directory not empty". The same wall an earlier fix hit with a bind-mounted HERMES_HOME.

    Cleanup is best-effort: a leftover directory in the temp dir is untidy, while an exception from a
    `finally` reports a failure that did not happen.
    """
    import shutil

    path = tempfile.mkdtemp(prefix="cs-contract-", dir=_probe_root())
    try:
        yield path
    finally:
        def _force(func, target, _exc):  # noqa: ANN001 - shutil's signature
            try:
                os.chmod(target, 0o700)
                func(target)
            except OSError:
                pass

        try:
            shutil.rmtree(path, onerror=_force)
        except OSError:
            pass


def _probe_root() -> Path:
    """A temp root that is not inside the user's real Hermes directory.

    TMPDIR here can point inside ~/.hermes, and the whole point is that the probe never writes
    there. Prefer the system temp dir; fall back to the repo's gitignored staging area.
    """
    real = (Path.home() / ".hermes").resolve()
    for candidate in (Path(tempfile.gettempdir()), Path("/tmp")):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved == real or real in resolved.parents:
            continue
        if os.access(resolved, os.W_OK):
            return candidate
    raise RuntimeError("no temp directory outside the real Hermes home is writable")


def probe(hermes_bin: str = "hermes", *, timeout: float = 180.0) -> dict[str, Any]:
    """Run one real one-shot and report what happened.

    Returns a dict with ``ok``, ``reason`` and the detail needed to judge it. Never raises for an
    expected failure; a probe that did not work is a result.
    """
    from ..harness.adapters import HermesHarness
    from ..harness.base import AgentTask, Budget

    answer = '{"status": "changed", "summary": "probe ok"}'
    schema = {
        "type": "object",
        "required": ["status", "summary"],
        "properties": {
            "status": {"type": "string", "enum": ["changed", "no_change", "declined"]},
            "summary": {"type": "string"},
        },
    }

    with _probe_dir() as tmp:
        home = Path(tmp) / "home"
        home.mkdir()
        _assert_isolated(home)

        with _FakeProvider(answer) as provider:
            config = (
                f"model: probe-model\n"
                f"provider: csprobe\n"
                f"providers:\n"
                f"  csprobe:\n"
                f"    type: openai_compat\n"
                f"    base_url: {provider.url}/v1\n"
                f"    api_key_env: CS_PROBE_KEY\n"
                f"    models:\n"
                f"      - probe-model\n"
            )
            (home / "config.yaml").write_text(config, encoding="utf-8")
            (home / ".env").write_text("CS_PROBE_KEY=placeholder-not-a-credential\n", encoding="utf-8")

            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": str(home),
                "HERMES_HOME": str(home),
                "LANG": "C.UTF-8",
                "NO_COLOR": "1",
                "TMPDIR": tmp,
            }

            harness = HermesHarness(
                hermes_bin=hermes_bin,
                extra_argv=["--provider", "csprobe", "--model", "probe-model"],
            )
            task = AgentTask(
                kind="fix",
                prompt="Reply with the JSON object only.",
                workdir=home,
                output_schema=schema,
                budget=Budget(max_seconds=int(timeout) - 20, max_turns=1),
            )
            result = harness.run(task, env)
            requests = len(provider.requests)

    if requests == 0:
        return {
            "ok": False,
            "reason": "the provider was never called, so the CLI contract is unproven",
            "provider_requests": 0,
            "detail": (result.error or result.raw_tail or "")[-600:],
        }
    if not result.ok:
        return {
            "ok": False,
            "reason": f"the one-shot did not produce usable output: {result.error}",
            "provider_requests": requests,
            "detail": (result.raw_tail or "")[-600:],
        }
    if result.output != json.loads(answer):
        return {
            "ok": False,
            "reason": "the parsed object did not match what the provider returned",
            "provider_requests": requests,
            "detail": json.dumps(result.output)[:300],
        }
    return {
        "ok": True,
        "reason": f"one-shot completed; provider called {requests}x; output parsed and validated",
        "provider_requests": requests,
        "detail": json.dumps(result.output),
    }
