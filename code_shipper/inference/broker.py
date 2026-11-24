"""Host-side inference broker.

The broker is the only place a provider credential exists. A contained worker never receives
that credential; it receives a *capability*: a random token scoped to one run, one provider/model
pair, a request count, a prompt size, an output-token ceiling and an expiry.

Protocol (one request per connection, newline-terminated JSON, one JSON line back):

    -> {"op": "complete", "capability": "...", "provider": "...", "model": "...",
        "messages": [{"role": "user", "content": "..."}], "max_tokens": 256}
    <- {"ok": true, "text": "...", "usage": {...}}
    <- {"ok": false, "error": "<code>"}

Design rules this module enforces:

* Only ``op == "complete"`` is served. Anything else is refused, so the broker is not a shell,
  a file server, a proxy or a credential source.
* Unknown fields are refused rather than ignored, so a caller cannot smuggle ``path``, ``argv``
  or ``url`` through to something that might later read them.
* Every failure path returns ``ok: false`` and no text. There is no partial or synthesised
  completion, and a broker that cannot record a receipt or cannot reach its upstream refuses the
  request instead of degrading.
* Nothing secret is ever echoed: refusals carry a fixed code and never the payload, the ledger
  stores hashes and counts, never prompts or completions.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import stat
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

MAX_REQUEST_BYTES = 64 * 1024
MAX_MESSAGES = 64
MAX_MESSAGE_CHARS = 16 * 1024
MAX_OUTPUT_TOKENS_CEILING = 8192
REQUEST_OP = "complete"
ALLOWED_ROLES = frozenset({"system", "user", "assistant", "tool"})
TOP_LEVEL_KEYS = frozenset(
    {"op", "capability", "provider", "model", "messages", "max_tokens", "tools", "tool_choice"}
)
MAX_TOOLS = 32
MAX_TOOL_SCHEMA_CHARS = 16 * 1024


class Failure(Exception):
    """A refusal. ``code`` is a fixed string; it never contains payload data."""

    code = "broker_error"

    def __init__(self, code: str | None = None) -> None:
        super().__init__(code or self.code)
        if code:
            self.code = code


class StartupError(Exception):
    """The broker cannot run safely, so it refuses to start at all."""


@dataclass
class Completion:
    text: str
    usage: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] | None = None
    finish_reason: str | None = None


class Upstream(Protocol):
    """A model provider the broker speaks to. Runs on the host, holds the credential."""

    def complete(
        self,
        *,
        provider: str,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        timeout: float,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
    ) -> Completion: ...


@dataclass
class Capability:
    """A per-run, bounded permission to ask for completions."""

    token: str
    run_id: str
    provider: str
    model: str
    max_requests: int = 8
    max_prompt_chars: int = 24000
    max_output_tokens: int = 1024
    expires_at: float = 0.0
    revoked: bool = False
    requests_used: int = 0
    tokens_used: int = 0

    def public(self) -> dict[str, Any]:
        """The capability as the broker may report it (never includes the token)."""
        return {
            "run_id": self.run_id,
            "provider": self.provider,
            "model": self.model,
            "max_requests": self.max_requests,
            "requests_used": self.requests_used,
            "max_output_tokens": self.max_output_tokens,
            "expires_at": self.expires_at,
            "revoked": self.revoked,
        }


@dataclass
class Receipt:
    ts: float
    run_id: str
    outcome: str
    provider: str
    model: str
    prompt_sha256: str
    prompt_chars: int
    max_tokens: int
    completion_chars: int
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": round(self.ts, 3),
            "run_id": self.run_id,
            "outcome": self.outcome,
            "provider": self.provider,
            "model": self.model,
            "prompt_sha256": self.prompt_sha256,
            "prompt_chars": self.prompt_chars,
            "max_tokens": self.max_tokens,
            "completion_chars": self.completion_chars,
            "error": self.error,
        }


class Broker:
    """Serves model completions to callers that hold a capability token."""

    def __init__(
        self,
        upstream: Upstream | None,
        *,
        ledger_path: str | os.PathLike[str] | None = None,
        clock: Callable[[], float] = time.time,
        default_timeout: float = 120.0,
    ) -> None:
        self._upstream = upstream
        self._clock = clock
        self._default_timeout = default_timeout
        self._capabilities: dict[str, Capability] = {}
        self._lock = threading.Lock()
        self._fatal: str | None = None
        self._receipts: list[Receipt] = []
        self._ledger = Path(ledger_path) if ledger_path is not None else None
        if self._ledger is not None:
            try:
                self._ledger.parent.mkdir(parents=True, exist_ok=True)
                self._ledger.touch(exist_ok=True)
            except OSError as exc:
                raise StartupError(f"ledger not writable: {exc.strerror}") from None
        if upstream is None:
            self._fatal = "no_upstream"

    # -- lifecycle ---------------------------------------------------------

    @property
    def failed(self) -> str | None:
        return self._fatal

    def fail(self, reason: str) -> None:
        """Mark the broker unhealthy: from now on it refuses every request."""
        self._fatal = reason

    # -- capabilities ------------------------------------------------------

    def issue_capability(
        self,
        *,
        run_id: str,
        provider: str,
        model: str,
        max_requests: int = 8,
        max_prompt_chars: int = 24000,
        max_output_tokens: int = 1024,
        ttl_seconds: float = 3600.0,
        token: str | None = None,
    ) -> Capability:
        for name, value in (
            ("max_requests", max_requests),
            ("max_prompt_chars", max_prompt_chars),
            ("max_output_tokens", max_output_tokens),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if max_output_tokens > MAX_OUTPUT_TOKENS_CEILING:
            raise ValueError("max_output_tokens above the broker ceiling")
        cap = Capability(
            token=token or secrets.token_urlsafe(32),
            run_id=run_id,
            provider=provider,
            model=model,
            max_requests=max_requests,
            max_prompt_chars=max_prompt_chars,
            max_output_tokens=max_output_tokens,
            expires_at=self._clock() + ttl_seconds,
        )
        with self._lock:
            self._capabilities[cap.token] = cap
        return cap

    def revoke(self, token: str) -> bool:
        with self._lock:
            cap = self._capabilities.get(token)
            if cap is None:
                return False
            cap.revoked = True
            return True

    def capability(self, token: str) -> Capability | None:
        with self._lock:
            return self._capabilities.get(token)

    # -- request handling --------------------------------------------------

    def handle(self, raw: bytes) -> dict[str, Any]:
        """Validate and serve one request. Never raises; always returns a response dict."""
        try:
            return self._handle(raw)
        except Failure as exc:
            return {"ok": False, "error": exc.code}
        except Exception:  # noqa: BLE001 - fail closed on anything unforeseen
            self.fail("internal_error")
            return {"ok": False, "error": "internal_error"}

    def _handle(self, raw: bytes) -> dict[str, Any]:
        if self._fatal is not None:
            raise Failure("broker_failed")
        if not isinstance(raw, (bytes, bytearray)):
            raise Failure("bad_request")
        if len(raw) > MAX_REQUEST_BYTES:
            raise Failure("request_too_large")
        payload = _decode(raw)
        request = _validate(payload)
        cap = self._authorise(request["capability"])
        self._check_scope(cap, request)
        prompt_chars = sum(
            len(m.get("content") or "")
            + sum(len(c["function"]["arguments"]) for c in (m.get("tool_calls") or []))
            for m in request["messages"]
        )
        if prompt_chars > cap.max_prompt_chars:
            raise Failure("prompt_too_large")
        self._reserve(cap, prompt_chars, request["max_tokens"])

        prompt_sha = hashlib.sha256(
            json.dumps(request["messages"], sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

        if self._upstream is None:
            self._record(cap, request, prompt_sha, prompt_chars, 0, "refused", "no_upstream")
            raise Failure("upstream_unavailable")

        started = self._clock()
        try:
            completion = self._upstream.complete(
                provider=cap.provider,
                model=cap.model,
                messages=request["messages"],
                max_tokens=request["max_tokens"],
                timeout=self._default_timeout,
                tools=request.get("tools"),
                tool_choice=request.get("tool_choice"),
            )
        except Exception:  # noqa: BLE001 - any upstream failure is a refusal, not a fallback
            self._record(cap, request, prompt_sha, prompt_chars, 0, "refused", "upstream_error")
            raise Failure("upstream_error") from None

        text = getattr(completion, "text", None)
        tool_calls = getattr(completion, "tool_calls", None)
        # A tool call legitimately has no text. Text OR a tool call, never neither.
        if not (isinstance(text, str) and text.strip()):
            if not isinstance(tool_calls, list) or not tool_calls:
                self._record(cap, request, prompt_sha, prompt_chars, 0, "refused", "empty_completion")
                raise Failure("upstream_error")
            text = ""

        elapsed = self._clock() - started
        self._record(cap, request, prompt_sha, prompt_chars, len(text), "confirmed", None)
        response: dict[str, Any] = {
            "ok": True,
            "text": text,
            "usage": {
                "prompt_chars": prompt_chars,
                "completion_chars": len(text),
                "seconds": round(elapsed, 3),
                "provider_usage": getattr(completion, "usage", None),
            },
        }
        if isinstance(tool_calls, list) and tool_calls:
            response["tool_calls"] = tool_calls
            response["finish_reason"] = getattr(completion, "finish_reason", None) or "tool_calls"
        return response

    def _authorise(self, token: str) -> Capability:
        """Validate the capability. Callers then reserve under the same lock (see _reserve),
        so a revoke cannot slip between the check and the send."""
        with self._lock:
            cap = self._capabilities.get(token)
            if cap is None:
                raise Failure("capability_unknown")
            if cap.revoked:
                raise Failure("capability_revoked")
            if self._clock() >= cap.expires_at:
                raise Failure("capability_expired")
            if cap.requests_used >= cap.max_requests:
                raise Failure("capability_exhausted")
            return cap

    def _check_scope(self, cap: Capability, request: dict[str, Any]) -> None:
        if request["provider"] != cap.provider or request["model"] != cap.model:
            raise Failure("model_not_allowed")
        if request["max_tokens"] > cap.max_output_tokens:
            raise Failure("token_limit_exceeded")

    def _reserve(self, cap: Capability, prompt_chars: int, max_tokens: int) -> None:
        """Re-check and count the request in one critical section.

        The state can change between _authorise and here, so the guards are repeated: that is
        what makes a revoke or expiry win over an in-flight request rather than racing it.
        """
        with self._lock:
            if cap.revoked:
                raise Failure("capability_revoked")
            if self._clock() >= cap.expires_at:
                raise Failure("capability_expired")
            if cap.requests_used >= cap.max_requests:
                raise Failure("capability_exhausted")
            cap.requests_used += 1
            cap.tokens_used += prompt_chars + max_tokens

    def _record(
        self,
        cap: Capability,
        request: dict[str, Any],
        prompt_sha: str,
        prompt_chars: int,
        completion_chars: int,
        outcome: str,
        error: str | None,
    ) -> None:
        receipt = Receipt(
            ts=self._clock(),
            run_id=cap.run_id,
            outcome=outcome,
            provider=cap.provider,
            model=cap.model,
            prompt_sha256=prompt_sha,
            prompt_chars=prompt_chars,
            max_tokens=request["max_tokens"],
            completion_chars=completion_chars,
            error=error,
        )
        with self._lock:
            self._receipts.append(receipt)
        if self._ledger is None:
            return
        try:
            with self._ledger.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(receipt.as_dict(), sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            # A receipt we cannot write is a run we cannot audit, so stop serving and refuse this
            # request: an unrecorded completion is not returned to the caller.
            self.fail("ledger_write_failed")
            raise Failure("receipt_write_failed") from None

    # -- socket server -----------------------------------------------------

    def serve_socket(
        self, path: str | os.PathLike[str], *, mode: int = 0o600, max_connections: int = 64
    ) -> "SocketServer":
        """``mode`` defaults to owner-only. Callers who need another UID to connect must widen it
        deliberately, and that choice is then visible at the call site."""
        return SocketServer(self, path, mode=mode, max_connections=max_connections)


class SocketServer:
    """Unix-socket front end. One request per connection, one JSON line back."""

    def __init__(self, broker: Broker, path: str | os.PathLike[str], *, mode: int, max_connections: int):
        self.broker = broker
        self.path = Path(path)
        self.mode = mode
        self.max_connections = max_connections
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._slots = threading.BoundedSemaphore(max_connections)
        self._count_lock = threading.Lock()
        self.served = 0

    def start(self) -> "SocketServer":
        if len(str(self.path)) > 100:
            raise StartupError("socket path too long for AF_UNIX")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _clear_stale_socket(self.path)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self.path))
        os.chmod(self.path, self.mode)
        sock.listen(self.max_connections)
        sock.settimeout(0.2)
        self._sock = sock
        self._thread = threading.Thread(target=self._loop, name="broker-accept", daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                break
            if not self._slots.acquire(blocking=False):
                _send(conn, {"ok": False, "error": "busy"})
                _close(conn)
                continue
            threading.Thread(target=self._handle_conn, args=(conn,), daemon=True).start()

    def _handle_conn(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(30.0)
            raw, oversized = _read_request(conn)
            response = (
                {"ok": False, "error": "request_too_large"}
                if oversized
                else self.broker.handle(raw)
            )
            if oversized:
                # The caller is still sending. Drain before replying so the close does not reset
                # the connection and discard the refusal we just wrote.
                _drain(conn)
            _send(conn, response)
            with self._count_lock:
                self.served += 1
        finally:
            _close(conn)
            self._slots.release()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._sock is not None:
            self._sock.close()
        try:
            self.path.unlink()
        except OSError:
            pass

    def __enter__(self) -> "SocketServer":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


def _read_request(conn: socket.socket) -> tuple[bytes, bool]:
    """Read one newline-terminated request. Returns (payload, oversized).

    Never returns more than the cap: the caller is told ``request_too_large`` over a live
    connection rather than seeing it dropped as a reset.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = conn.recv(4096)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_REQUEST_BYTES:
            return b"", True
        chunks.append(chunk)
        if b"\n" in chunk:
            break
    data = b"".join(chunks)
    return data.split(b"\n", 1)[0], False


def _drain(conn: socket.socket, *, seconds: float = 5.0) -> None:
    """Read and discard what the peer is still sending, so our refusal is delivered not reset.

    Bounded by time rather than by bytes: the caller may legitimately have a multi-megabyte
    buffer in flight, and cutting it off is what produces the reset we are trying to avoid.
    """
    deadline = time.monotonic() + seconds
    try:
        conn.settimeout(min(1.0, seconds))
        while time.monotonic() < deadline:
            chunk = conn.recv(65536)
            if not chunk:
                return
            if b"\n" in chunk:
                return
    except OSError:
        pass


def _close(conn: socket.socket) -> None:
    try:
        conn.close()
    except OSError:
        pass


def _clear_stale_socket(path: Path) -> None:
    """Remove a leftover socket file, but never delete a plain file somebody else placed here.

    A stale socket is safe to unlink: nothing is listening on it. Anything else at this path is
    either a mistake or an attempt to make us remove something, so refuse and let the operator
    deal with it.
    """
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(info.st_mode):
        raise StartupError(f"refusing to replace a non-socket file at {path}")
    path.unlink()


def _send(conn: socket.socket, response: dict[str, Any]) -> None:
    try:
        conn.sendall(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
    except OSError:
        pass


def _decode(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise Failure("malformed_json") from None
    if not isinstance(payload, dict):
        raise Failure("bad_request")
    return payload


def _validate(payload: dict[str, Any]) -> dict[str, Any]:
    # The operation is the discriminant, so it is checked first: a caller asking for a shell or a
    # file read is told so, rather than being told its field name is unknown.
    if payload.get("op") != REQUEST_OP:
        raise Failure("unsupported_operation")
    extra = set(payload) - TOP_LEVEL_KEYS
    if extra:
        # Refuse rather than ignore: unknown fields are how a caller would ask for a shell,
        # a file read or an arbitrary URL a later version might honour.
        raise Failure("unknown_field")

    token = payload.get("capability")
    if not isinstance(token, str) or not token:
        raise Failure("capability_required")
    provider = payload.get("provider")
    model = payload.get("model")
    if not isinstance(provider, str) or not isinstance(model, str) or not provider or not model:
        raise Failure("bad_request")

    max_tokens = payload.get("max_tokens")
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
        raise Failure("bad_request")
    if max_tokens > MAX_OUTPUT_TOKENS_CEILING:
        raise Failure("token_limit_exceeded")

    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise Failure("bad_request")
    if len(messages) > MAX_MESSAGES:
        raise Failure("too_many_messages")
    clean = [_validate_message(message) for message in messages]

    tools = _validate_tools(payload.get("tools"))
    tool_choice = payload.get("tool_choice")
    if tool_choice is not None and not isinstance(tool_choice, (str, dict)):
        raise Failure("bad_request")

    result = {
        "capability": token,
        "provider": provider,
        "model": model,
        "max_tokens": max_tokens,
        "messages": clean,
    }
    if tools:
        result["tools"] = tools
    if tool_choice is not None:
        result["tool_choice"] = tool_choice
    return result


def _validate_message(message: object) -> dict[str, Any]:
    """Validate one chat message, including the shapes a tool round-trip needs.

    Allowed shapes:
      {role, content}                              system / user / assistant, content a string
      {role, content=null, tool_calls}             an assistant that asked for tools
      {role, content, tool_call_id}                a tool result for a specific call

    ``tool_calls`` entries are constrained to id/type/function with string arguments, so a message
    cannot carry executable structure through to the provider.
    """
    if not isinstance(message, dict):
        raise Failure("bad_request")
    # Strict on purpose. Only the fields a validated tool round-trip needs; "name" is not one,
    # and the probe correctly flags it as an unknown key.
    allowed = {"role", "content", "tool_calls", "tool_call_id"}
    if set(message) - allowed:
        raise Failure("bad_request")

    role = message.get("role")
    if role not in ALLOWED_ROLES:
        raise Failure("bad_request")

    content = message.get("content")
    if content is not None and not isinstance(content, str):
        # Reject content parts: images and audio are not part of this channel.
        raise Failure("unsupported_content")
    if isinstance(content, str) and len(content) > MAX_MESSAGE_CHARS:
        raise Failure("prompt_too_large")
    if content is None and role != "assistant":
        raise Failure("bad_request")

    clean: dict[str, Any] = {"role": role, "content": content if content is not None else ""}

    tool_calls = message.get("tool_calls")
    if tool_calls is not None:
        if role != "assistant":
            raise Failure("bad_request")
        clean["tool_calls"] = _validate_tool_calls(tool_calls)
        clean["content"] = content  # an assistant tool call legitimately has no text

    tool_call_id = message.get("tool_call_id")
    if role == "tool":
        # A tool result is only meaningful as the answer to a specific call. Without the id it is
        # not a tool result, it is a malformed message, so refuse rather than pass it upstream.
        if not isinstance(tool_call_id, str) or not tool_call_id or len(tool_call_id) > 128:
            raise Failure("bad_request")
        clean["tool_call_id"] = tool_call_id
    elif tool_call_id is not None:
        raise Failure("bad_request")

    return clean


def _validate_tool_calls(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise Failure("bad_request")
    if len(raw) > MAX_TOOLS:
        raise Failure("too_many_tools")
    calls: list[dict[str, Any]] = []
    for call in raw:
        if not isinstance(call, dict):
            raise Failure("bad_request")
        if set(call) - {"id", "type", "function"}:
            raise Failure("bad_request")
        function = call.get("function")
        if not isinstance(function, dict) or set(function) - {"name", "arguments"}:
            raise Failure("bad_request")
        name = function.get("name")
        arguments = function.get("arguments")
        if not isinstance(name, str) or not name or len(name) > 64:
            raise Failure("bad_request")
        if not isinstance(arguments, str) or len(arguments) > MAX_MESSAGE_CHARS:
            raise Failure("bad_request")
        calls.append(
            {
                "id": call.get("id") if isinstance(call.get("id"), str) else "",
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        )
    return calls


def _validate_tools(raw: object) -> list[dict[str, Any]]:
    """Tool definitions are data, not code. Bounded, named, and restricted to function tools."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise Failure("bad_request")
    if len(raw) > MAX_TOOLS:
        raise Failure("too_many_tools")
    tools: list[dict[str, Any]] = []
    for tool in raw:
        if not isinstance(tool, dict) or set(tool) != {"type", "function"}:
            raise Failure("bad_request")
        if tool["type"] != "function":
            # No code interpreter, no retrieval, no provider-hosted tool of any kind.
            raise Failure("unsupported_tool_type")
        function = tool["function"]
        if not isinstance(function, dict):
            raise Failure("bad_request")
        if set(function) - {"name", "description", "parameters"}:
            raise Failure("bad_request")
        name = function.get("name")
        if not isinstance(name, str) or not name or len(name) > 64:
            raise Failure("bad_request")
        description = function.get("description", "")
        if not isinstance(description, str) or len(description) > 2048:
            raise Failure("bad_request")
        parameters = function.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise Failure("bad_request")
        if len(json.dumps(parameters)) > MAX_TOOL_SCHEMA_CHARS:
            raise Failure("tool_schema_too_large")
        tools.append(
            {"type": "function",
             "function": {"name": name, "description": description, "parameters": parameters}}
        )
    return tools


def read_secret_file(path: str | os.PathLike[str]) -> str:
    """Read a credential the broker owns. Missing, empty or unreadable is a startup failure."""
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise StartupError(f"credential file unreadable: {exc.strerror}") from None
    if not value:
        raise StartupError("credential file empty")
    return value
