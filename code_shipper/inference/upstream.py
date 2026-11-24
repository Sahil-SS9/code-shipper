"""Provider clients the broker owns. These run on the host, next to the credential.

Only an OpenAI-compatible chat-completions client is implemented: it covers the local serving
stack and every hosted provider that speaks the same shape. Adding another wire format means a
new class here, not a change in the broker's protocol.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .broker import Completion


class UpstreamError(Exception):
    """The provider refused or failed. The broker turns this into ``upstream_error``."""


class OpenAICompatUpstream:
    """POST /v1/chat/completions with a bearer token the broker holds in memory."""

    def __init__(self, base_url: str, api_key: str, *, required: bool = True) -> None:
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        self.base_url = base
        self._api_key = api_key
        self._required = required

    def __repr__(self) -> str:  # never leak the key through a repr in a traceback
        return f"OpenAICompatUpstream(base_url={self.base_url!r}, api_key=<redacted>)"

    def complete(
        self,
        *,
        provider: str,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        timeout: float,
    ) -> Completion:
        body = json.dumps(
            {"model": model, "messages": messages, "max_tokens": max_tokens, "stream": False}
        ).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "code-shipper-broker/0.0.1"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        elif self._required:
            raise UpstreamError("no credential configured")

        request = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions", data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001
                pass
            raise UpstreamError(f"http {exc.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise UpstreamError(f"transport: {exc}") from None
        except json.JSONDecodeError:
            raise UpstreamError("malformed provider response") from None

        if not isinstance(payload, dict):
            raise UpstreamError("unexpected provider payload")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise UpstreamError("no choices in provider response")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str):
            raise UpstreamError("no text in provider response")
        usage = payload.get("usage")
        return Completion(text=text, usage=usage if isinstance(usage, dict) else None)


class StaticUpstream:
    """A fixed response. Tests and dry runs only; it holds no credential and reaches no network."""

    def __init__(self, text: str, usage: dict | None = None) -> None:
        self.text = text
        self.usage = usage
        self.calls: list[dict] = []

    def complete(
        self,
        *,
        provider: str,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        timeout: float,
    ) -> Completion:
        self.calls.append(
            {"provider": provider, "model": model, "messages": messages, "max_tokens": max_tokens}
        )
        return Completion(text=self.text, usage=self.usage)


class FailingUpstream:
    """Always fails, so the fail-closed paths can be exercised without a network."""

    def __init__(self, reason: str = "boom") -> None:
        self.reason = reason

    def complete(self, **_: object) -> Completion:
        raise UpstreamError(self.reason)
