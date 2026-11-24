"""Provider clients the broker owns. These run on the host, next to the credential.

Only an OpenAI-compatible chat-completions client is implemented: it covers the local serving
stack and every hosted provider that speaks the same shape. Adding another wire format means a
new class here, not a change in the broker's protocol.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .broker import Completion


class UpstreamError(Exception):
    """The provider refused or failed. The broker turns this into ``upstream_error``."""


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Any 3xx is a refusal, not a hop.

    urllib's default handler follows redirects and resends the original headers, including
    ``Authorization``, to whatever host the redirect names. A provider that answers 302 would
    therefore hand our key to an arbitrary host. Refusing every redirect keeps the credential
    pinned to the configured base URL.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        raise UpstreamError(f"provider redirect refused: {code}")


class OpenAICompatUpstream:
    """POST /v1/chat/completions with a bearer token the broker holds in memory."""

    def __init__(self, base_url: str, api_key: str, *, required: bool = True) -> None:
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        self.base_url = base
        self._api_key = api_key
        self._required = required
        # An opener with no redirect handler at all: build_opener() adds only the defaults it is
        # not given, so supplying this one replaces the following behaviour outright.
        self._opener = urllib.request.build_opener(_RefuseRedirects)

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
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
    ) -> Completion:
        body_dict: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            body_dict["tools"] = tools
        if tool_choice is not None:
            body_dict["tool_choice"] = tool_choice
        body = json.dumps(body_dict).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "code-shipper-broker/0.0.1"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        elif self._required:
            raise UpstreamError("no credential configured")

        request = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions", data=body, headers=headers, method="POST"
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
        except UpstreamError:
            # Raised by the redirect handler. Never followed, so the key never travels.
            raise
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
        tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
        finish = choices[0].get("finish_reason") if isinstance(choices[0], dict) else None
        if not isinstance(text, str):
            text = ""
        if not isinstance(tool_calls, list):
            tool_calls = None
        if not text.strip() and not tool_calls:
            raise UpstreamError("no text and no tool calls in provider response")
        usage = payload.get("usage")
        return Completion(
            text=text,
            usage=usage if isinstance(usage, dict) else None,
            tool_calls=tool_calls,
            finish_reason=finish if isinstance(finish, str) else None,
        )


class StaticUpstream:
    """A fixed response. Tests and dry runs only; it holds no credential and reaches no network."""

    def __init__(self, text: str, usage: dict | None = None, tool_calls: list | None = None) -> None:
        self.text = text
        self.usage = usage
        self.tool_calls = tool_calls
        self.calls: list[dict] = []

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
    ) -> Completion:
        self.calls.append(
            {
                "provider": provider,
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
        return Completion(text=self.text, usage=self.usage, tool_calls=self.tool_calls)


class FailingUpstream:
    """Always fails, so the fail-closed paths can be exercised without a network."""

    def __init__(self, reason: str = "boom") -> None:
        self.reason = reason

    def complete(self, **_: object) -> Completion:
        raise UpstreamError(self.reason)
