"""The broker: the only component that talks to a code host with a credential.

Every write goes through here, and the role decides what is allowed before any request is built. That
ordering is the whole point: a refusal must happen in this process, not as an HTTP error from GitHub,
because a 4xx from the host proves the host's policy rather than ours.

Roles and their limits are from tech spec §5.1. The important ones:

* ``reviewer`` may create a review with event=COMMENT only. APPROVE and REQUEST_CHANGES are refused
  here, before a request exists, because the boundary is ours to enforce.
* ``fixer`` may push to a PR head branch, fast-forward only.
* ``approver`` may set the approvals status and approve, and may never push.
* No role has a merge operation. There is no merge method in this module at all.

``Transport`` is the seam tests use to count outbound requests: a refused operation must produce zero.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

API_ROOT = "https://api.github.com"
USER_AGENT = "code-shipper-broker/0.1"

# The only review events a reviewer role may send. APPROVE is absent on purpose.
REVIEWER_EVENTS = frozenset({"COMMENT"})


class BrokerRefused(Exception):
    """The broker refused an operation. Raised before any outbound request is made."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class BrokerError(Exception):
    """The request was allowed but the host failed."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Transport(Protocol):
    def post(self, url: str, body: dict, headers: dict[str, str]) -> tuple[int, dict]: ...


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        raise BrokerError(f"redirect refused: {code}", status=code)


class UrllibTransport:
    """The real transport. One POST, no redirects, no retries."""

    def __init__(self, timeout: float = 20.0) -> None:
        self._timeout = timeout
        self._opener = urllib.request.build_opener(_RefuseRedirects)

    def post(self, url: str, body: dict, headers: dict[str, str]) -> tuple[int, dict]:
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={**headers, "User-Agent": USER_AGENT},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read()
                return response.status, json.loads(raw or b"{}")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001
                pass
            raise BrokerError(f"HTTP {exc.code}: {detail}", status=exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BrokerError(f"transport failure: {exc}") from None


@dataclass
class Receipt:
    role: str
    op: str
    repo: str
    pr: int | None
    head: str | None
    status: int | None
    result: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "op": self.op,
            "repo": self.repo,
            "pr": self.pr,
            "head": self.head,
            "status": self.status,
            "result": self.result,
            "detail": self.detail,
        }


@dataclass
class Broker:
    """Owns the write credential and enforces the role allowlist."""

    token: str
    repo: str
    transport: Transport = field(default_factory=UrllibTransport)
    api_root: str = API_ROOT
    receipts: list[Receipt] = field(default_factory=list)
    ledger_path: Path | None = None

    def __repr__(self) -> str:
        return f"Broker(repo={self.repo!r}, token=<redacted>)"

    # -- reviews -----------------------------------------------------------

    def post_review(
        self,
        *,
        pr: int,
        head_sha: str,
        body: str,
        event: str = "COMMENT",
        comments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create one review. Only COMMENT is permitted for the reviewer role.

        The event check happens first, before the URL is built, so a refused approve costs zero
        outbound requests. That is what makes this our boundary rather than GitHub's: a 422 from the
        host would prove only that GitHub dislikes self-approval.
        """
        if event not in REVIEWER_EVENTS:
            self._record("reviewer", "review", self.repo, pr, head_sha, None, "refused")
            raise BrokerRefused(
                f"reviewer role may only send event=COMMENT; {event!r} is refused before any request"
            )
        if not isinstance(body, str) or not body.strip():
            self._record("reviewer", "review", self.repo, pr, head_sha, None, "refused")
            raise BrokerRefused("a review needs a non-empty body")

        payload: dict[str, Any] = {"commit_id": head_sha, "body": body, "event": "COMMENT"}
        if comments:
            payload["comments"] = [self._comment_payload(entry) for entry in comments]

        url = f"{self.api_root}/repos/{self.repo}/pulls/{int(pr)}/reviews"
        status, data = self.transport.post(url, payload, self._headers())
        self._record("reviewer", "review", self.repo, pr, head_sha, status, "confirmed")
        return data

    def _comment_payload(self, entry: dict[str, Any]) -> dict[str, Any]:
        path = entry.get("path")
        line = entry.get("line")
        body = entry.get("body")
        if not isinstance(path, str) or not path:
            raise BrokerRefused("an inline comment needs a path")
        if not isinstance(line, int) or line <= 0:
            raise BrokerRefused("an inline comment needs a positive line")
        if not isinstance(body, str) or not body.strip():
            raise BrokerRefused("an inline comment needs a body")
        return {"path": path, "line": line, "side": "RIGHT", "body": body}

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _record(
        self,
        role: str,
        op: str,
        repo: str,
        pr: int | None,
        head: str | None,
        status: int | None,
        result: str,
        detail: str = "",
    ) -> None:
        receipt = Receipt(role, op, repo, pr, head, status, result, detail)
        self.receipts.append(receipt)
        if self.ledger_path is None:
            return
        try:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(receipt.as_dict(), sort_keys=True) + "\n")
                handle.flush()
        except OSError:
            # A write we cannot record is one we cannot audit; stop rather than continue blind.
            raise BrokerError("could not write the broker receipt") from None


class CountingTransport:
    """A transport that records what it was asked to send and never reaches the network.

    The count is the point: a refused operation must leave it at zero.
    """

    def __init__(self, response: tuple[int, dict] | None = None, error: Exception | None = None):
        self.requests: list[tuple[str, dict, dict[str, str]]] = []
        self._response = response or (200, {"id": 1, "state": "COMMENTED"})
        self._error = error

    def post(self, url: str, body: dict, headers: dict[str, str]) -> tuple[int, dict]:
        self.requests.append((url, body, headers))
        if self._error is not None:
            raise self._error
        return self._response

    @property
    def count(self) -> int:
        return len(self.requests)


def quote_path(path: str) -> str:
    return urllib.parse.quote(path, safe="/")
