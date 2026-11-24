"""The worker-side client. It knows a socket path and a capability token, nothing else.

This is the only inference code that exists inside the container. It holds no provider
credential, no base URL and no retry logic that could outlive a run: if the socket is gone, the
call fails.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from typing import Any

ENV_SOCKET = "CODE_SHIPPER_INFERENCE_SOCKET"
ENV_CAPABILITY = "CODE_SHIPPER_INFERENCE_CAPABILITY"
ENV_PROVIDER = "CODE_SHIPPER_INFERENCE_PROVIDER"
ENV_MODEL = "CODE_SHIPPER_INFERENCE_MODEL"


class InferenceUnavailable(Exception):
    """No completion. The caller must not continue as though it got one."""


@dataclass
class BrokerClient:
    socket_path: str
    capability: str
    provider: str
    model: str
    timeout: float = 180.0

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "BrokerClient":
        env = os.environ if environ is None else environ
        missing = [
            name
            for name in (ENV_SOCKET, ENV_CAPABILITY, ENV_PROVIDER, ENV_MODEL)
            if not env.get(name)
        ]
        if missing:
            raise InferenceUnavailable(f"missing broker configuration: {', '.join(missing)}")
        return cls(
            socket_path=env[ENV_SOCKET],
            capability=env[ENV_CAPABILITY],
            provider=env[ENV_PROVIDER],
            model=env[ENV_MODEL],
        )

    def complete(self, prompt: str, *, max_tokens: int = 512, system: str | None = None) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = self.request(messages, max_tokens=max_tokens)
        text = response.get("text")
        if not isinstance(text, str):
            raise InferenceUnavailable("broker returned no text")
        return text

    def request(self, messages: list[dict[str, str]], *, max_tokens: int) -> dict[str, Any]:
        payload = {
            "op": "complete",
            "capability": self.capability,
            "provider": self.provider,
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
        except OSError as exc:
            # Fail closed: a stopped broker is an unavailable model, never a cached or fake answer.
            sock.close()
            raise InferenceUnavailable(f"broker unreachable: {exc.strerror or exc}") from None
        try:
            sock.sendall(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
            raw = _read_line(sock)
        finally:
            sock.close()
        if not raw:
            raise InferenceUnavailable("broker closed the connection without a response")
        try:
            response = json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            raise InferenceUnavailable("broker sent a malformed response") from None
        if not isinstance(response, dict) or not response.get("ok"):
            error = response.get("error") if isinstance(response, dict) else None
            raise InferenceUnavailable(f"broker refused: {error or 'unknown'}")
        return response


def _read_line(sock: socket.socket) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
        if b"\n" in chunk:
            break
    return b"".join(chunks).split(b"\n", 1)[0]
