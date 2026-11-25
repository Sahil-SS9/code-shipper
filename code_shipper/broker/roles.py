"""Broker roles, allowlists, exact-head checks and receipts (tech spec §5.1).

Every repository write goes through here. The rules are ordered so that a refusal happens before any
outbound request exists:

1. **Role allowed for this operation.** ``reviewer`` may comment, ``fixer`` may push to a PR head
   branch, ``approver`` may set the approvals status. No role may merge, and there is no merge
   operation in this module at all.
2. **Repo and PR in the allowlist.** A broker configured for one repository refuses another.
3. **Exact head.** A write that depends on the head is refused unless the caller's expected SHA
   matches the head the broker last observed, so a write computed against a stale head cannot land.
4. **Then** the request is built, sent, and a receipt recorded.

The ledger states are ``planned``, ``posting``, ``confirmed``, ``refused`` and ``uncertain``. An
uncertain write is never replayed blindly: reconciliation re-reads the host and decides, because the
only two safe outcomes are "it landed" and "it definitely did not".
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

API_ROOT = "https://api.github.com"
USER_AGENT = "code-shipper-broker/0.1"

# What each role may do. A merge is absent from every set, deliberately: an operation that does not
# exist cannot be authorised by mistake.
ROLE_OPS: dict[str, frozenset[str]] = {
    "user": frozenset({"comment", "open_pr", "create_branch", "push_branch", "label"}),
    "reviewer": frozenset({"review_comment", "comment", "resolve_thread"}),
    "fixer": frozenset({"push_head", "comment", "reply_review", "label"}),
    "approver": frozenset({"set_status", "approve", "dismiss_approval"}),
}
FORBIDDEN_OPS = frozenset({"merge", "force_push", "delete_branch", "write_check_run", "push_tag"})
REVIEW_EVENTS_BY_ROLE = {
    "reviewer": frozenset({"COMMENT"}),
    "approver": frozenset({"APPROVE"}),
}


class BrokerRefused(Exception):
    """Refused before any outbound request. ``reason`` is a fixed code plus detail."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class BrokerError(Exception):
    """The request was allowed but the host failed."""

    def __init__(self, message: str, *, status: int | None = None, uncertain: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.uncertain = uncertain


class Transport(Protocol):
    def post(self, url: str, body: dict, headers: dict[str, str]) -> tuple[int, dict]: ...
    def delete(self, url: str, headers: dict[str, str]) -> tuple[int, dict]: ...


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        raise BrokerError(f"redirect refused: {code}", status=code)


class UrllibTransport:
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
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001
                pass
            # 5xx is uncertain: it may or may not have been applied.
            raise BrokerError(
                f"HTTP {exc.code}: {detail}", status=exc.code, uncertain=exc.code >= 500
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # A transport failure is uncertain for the same reason.
            raise BrokerError(f"transport failure: {exc}", uncertain=True) from None

    def delete(self, url: str, headers: dict[str, str]) -> tuple[int, dict]:
        """A real DELETE. GitHub's label endpoint needs one, and no method-override header works."""
        request = urllib.request.Request(
            url,
            headers={**headers, "User-Agent": USER_AGENT},
            method="DELETE",
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                body = response.read() or b"{}"
                return response.status, json.loads(body or b"{}")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001
                pass
            raise BrokerError(
                f"HTTP {exc.code}: {detail}", status=exc.code, uncertain=exc.code >= 500
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BrokerError(f"transport failure: {exc}", uncertain=True) from None


@dataclass
class Receipt:
    ts: float
    role: str
    op: str
    repo: str
    pr: int | None
    expected_head: str | None
    observed_head: str | None
    state: str
    request_hash: str
    response_status: int | None = None
    detail: str = ""
    op_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": round(self.ts, 3),
            "role": self.role,
            "op": self.op,
            "repo": self.repo,
            "pr": self.pr,
            "expected_head": self.expected_head,
            "observed_head": self.observed_head,
            "state": self.state,
            "request_hash": self.request_hash,
            "response_status": self.response_status,
            "detail": self.detail,
            "op_id": self.op_id,
        }


@dataclass
class Broker:
    """Owns the write credential, enforces the role allowlist, and records every attempt."""

    token: str
    repo: str
    transport: Transport = field(default_factory=UrllibTransport)
    api_root: str = API_ROOT
    allowed_repos: frozenset[str] | None = None
    ledger_path: Path | None = None
    clock: Callable[[], float] = time.time
    # The head the broker believes is current, per (repo, pr). Updated on observation and on push.
    observed_heads: dict[tuple[str, int], str] = field(default_factory=dict)
    receipts: list[Receipt] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.allowed_repos is None:
            self.allowed_repos = frozenset({self.repo})

    def __repr__(self) -> str:
        return f"Broker(repo={self.repo!r}, token=<redacted>)"

    # -- eligibility -------------------------------------------------------

    # The permission levels that may authorise a write. `read` and `none` cannot: a user who can only
    # read must not be able to steer a fix, whatever they type in a comment (T-ADV-03).
    WRITE_PERMISSIONS = frozenset({"admin", "maintain", "write"})

    @classmethod
    def permission_is_sufficient(cls, permission: str) -> bool:
        return permission in cls.WRITE_PERMISSIONS

    @classmethod
    def pr_is_eligible(cls, pr: Any) -> bool:
        """Whether a PR may be acted on at all.

        A fork PR is skipped (T-ADV-04): its head branch lives in another repository, so the fixer
        cannot push to it, and treating it as if it could would send a push at the wrong remote.
        """
        return not getattr(pr, "from_fork", False)

    # -- gating ------------------------------------------------------------

    def _authorise(self, role: str, op: str, *, repo: str, pr: int | None) -> None:
        """Gate an operation. A refusal is recorded, not just raised: it is audit evidence."""
        try:
            self._gate(role, op, repo=repo, pr=pr)
        except BrokerRefused as exc:
            self._record(
                role, op, pr, None, "refused", request_hash="",
                detail=f"{exc.code}" + (f": {exc.detail}" if exc.detail else ""),
            )
            raise

    def _gate(self, role: str, op: str, *, repo: str, pr: int | None) -> None:
        if role not in ROLE_OPS:
            raise BrokerRefused("unknown_role", role)
        if op in FORBIDDEN_OPS:
            # Belt and braces: these are absent from every role set anyway, and refusing by name here
            # means a future role added with a typo cannot quietly enable one.
            raise BrokerRefused("operation_not_permitted", f"{op} is never allowed")
        if op not in ROLE_OPS[role]:
            raise BrokerRefused("role_may_not", f"role {role!r} may not {op}")
        if repo not in (self.allowed_repos or frozenset()):
            raise BrokerRefused("repo_not_allowed", repo)
        if pr is not None and (not isinstance(pr, int) or pr <= 0):
            raise BrokerRefused("bad_pr", str(pr))

    def _check_head(self, *, repo: str, pr: int, expected_head: str | None, requires_head: bool) -> None:
        if expected_head is None:
            if requires_head:
                raise BrokerRefused("head_required", "this operation depends on the head")
            return
        observed = self.observed_heads.get((repo, pr))
        if observed is None:
            # Nothing observed yet: record the caller's claim so a later mismatch is detectable, but
            # do not invent agreement with it.
            self.observed_heads[(repo, pr)] = expected_head
            return
        if observed != expected_head:
            raise BrokerRefused(
                "head_moved", f"expected {expected_head[:12]}, observed {observed[:12]}"
            )

    # -- operations --------------------------------------------------------

    def comment(
        self, *, role: str, pr: int, body: str, expected_head: str | None = None
    ) -> dict[str, Any]:
        if not isinstance(body, str) or not body.strip():
            raise BrokerRefused("empty_body")
        self._authorise(role, "comment", repo=self.repo, pr=pr)
        self._check_head(repo=self.repo, pr=pr, expected_head=expected_head, requires_head=False)
        return self._send(
            role=role,
            op="comment",
            pr=pr,
            expected_head=expected_head,
            path=f"/repos/{self.repo}/issues/{pr}/comments",
            payload={"body": body},
        )

    def post_review(
        self,
        *,
        role: str = "reviewer",
        pr: int,
        head_sha: str,
        body: str,
        event: str = "COMMENT",
        comments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create a review. The event must be permitted for the role, checked before the request.

        A reviewer may only send COMMENT. An approver may only send APPROVE. Neither may merge.
        """
        if not isinstance(body, str) or not body.strip():
            self._record(role, "post_review", pr, head_sha, "refused", "",
                         detail="empty_body")
            raise BrokerRefused("empty_body")
        self._authorise(role, "review_comment" if role == "reviewer" else "approve",
                        repo=self.repo, pr=pr)
        permitted = REVIEW_EVENTS_BY_ROLE.get(role, frozenset())
        if event not in permitted:
            # Recorded as well as raised: a refused approve is exactly what an auditor looks for.
            self._record(role, "post_review", pr, head_sha, "refused", "",
                         detail=f"event_not_permitted: {event}")
            raise BrokerRefused(
                "event_not_permitted",
                f"role {role!r} may only send {sorted(permitted)}; {event!r} refused before any request",
            )
        self._check_head(repo=self.repo, pr=pr, expected_head=head_sha, requires_head=True)

        payload: dict[str, Any] = {"commit_id": head_sha, "body": body, "event": event}
        if comments:
            payload["comments"] = [self._inline(entry) for entry in comments]
        return self._send(
            role=role,
            op="post_review",
            pr=pr,
            expected_head=head_sha,
            path=f"/repos/{self.repo}/pulls/{pr}/reviews",
            payload=payload,
        )

    def remove_label(self, *, pr: int, label: str, role: str = "user") -> dict[str, Any]:
        """Remove a label. A real DELETE, through the same role allowlist as every other write."""
        if not isinstance(label, str) or not label.strip():
            raise BrokerRefused("empty_label")
        self._authorise(role, "label", repo=self.repo, pr=pr)
        return self._send(
            role=role,
            op="remove_label",
            pr=pr,
            expected_head=None,
            path=f"/repos/{self.repo}/issues/{pr}/labels/{urllib.parse.quote(label)}",
            payload={},
            method="DELETE",
        )

    def add_label(self, *, pr: int, label: str, role: str = "fixer") -> dict[str, Any]:
        """Add a label. Used to mark a PR as watched, not to gate anything."""
        if not isinstance(label, str) or not label.strip():
            raise BrokerRefused("empty_label")
        self._authorise(role, "label", repo=self.repo, pr=pr)
        return self._send(
            role=role,
            op="add_label",
            pr=pr,
            expected_head=None,
            path=f"/repos/{self.repo}/issues/{pr}/labels",
            payload={"labels": [label]},
        )

    def _inline(self, entry: dict[str, Any]) -> dict[str, Any]:
        path = entry.get("path")
        line = entry.get("line")
        body = entry.get("body")
        if not isinstance(path, str) or not path:
            raise BrokerRefused("bad_inline", "needs a path")
        if not isinstance(line, int) or line <= 0:
            raise BrokerRefused("bad_inline", "needs a positive line")
        if not isinstance(body, str) or not body.strip():
            raise BrokerRefused("bad_inline", "needs a body")
        return {"path": path, "line": line, "side": "RIGHT", "body": body}

    # -- sending -----------------------------------------------------------

    def _send(
        self,
        *,
        role: str,
        op: str,
        pr: int,
        expected_head: str | None,
        path: str,
        payload: dict[str, Any],
        method: str = "POST",
    ) -> dict[str, Any]:
        request_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()[:16]
        op_id = f"{op}-{request_hash[:8]}"
        self._record(role, op, pr, expected_head, "planned", request_hash, op_id=op_id)
        self._record(role, op, pr, expected_head, "posting", request_hash, op_id=op_id)
        try:
            if method == "DELETE":
                status, data = self.transport.delete(
                    f"{self.api_root}{path}", self._headers()
                )
            else:
                status, data = self.transport.post(
                    f"{self.api_root}{path}", payload, self._headers()
                )
        except BrokerError as exc:
            state = "uncertain" if exc.uncertain else "refused"
            self._record(
                role, op, pr, expected_head, state, request_hash,
                response_status=exc.status, detail=str(exc)[:200], op_id=op_id,
            )
            raise
        self._record(
            role, op, pr, expected_head, "confirmed", request_hash,
            response_status=status, op_id=op_id,
        )
        return data

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
        pr: int | None,
        expected_head: str | None,
        state: str,
        request_hash: str,
        *,
        response_status: int | None = None,
        detail: str = "",
        op_id: str = "",
    ) -> Receipt:
        receipt = Receipt(
            ts=self.clock(),
            role=role,
            op=op,
            repo=self.repo,
            pr=pr,
            expected_head=expected_head,
            observed_head=self.observed_heads.get((self.repo, pr)) if pr else None,
            state=state,
            request_hash=request_hash,
            response_status=response_status,
            detail=detail,
            op_id=op_id,
        )
        self.receipts.append(receipt)
        if self.ledger_path is not None:
            try:
                self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
                with self.ledger_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(receipt.as_dict(), sort_keys=True) + "\n")
                    handle.flush()
            except OSError:
                raise BrokerError("could not write the broker receipt", uncertain=True) from None
        return receipt

    # -- reconciliation ----------------------------------------------------

    def open_operations(self) -> list[Receipt]:
        """Operations left ``planned``, ``posting`` or ``uncertain`` — what a tick must settle."""
        latest: dict[str, Receipt] = {}
        for receipt in self.receipts:
            if receipt.op_id:
                latest[receipt.op_id] = receipt
        return [r for r in latest.values() if r.state in ("planned", "posting", "uncertain")]


def reconcile(
    broker: Broker,
    receipt: Receipt,
    *,
    observed: bool | None,
    note: str = "",
) -> Receipt:
    """Settle an uncertain operation by observation, never by replay.

    ``observed`` is what the host says happened: True if the effect is present, False if it
    definitely is not, None if it still cannot be determined. None leaves the operation uncertain —
    retrying on a guess is how a comment gets posted twice.
    """
    if observed is None:
        return broker._record(
            receipt.role, receipt.op, receipt.pr, receipt.expected_head, "uncertain",
            receipt.request_hash, detail=note or "still undetermined", op_id=receipt.op_id,
        )
    state = "confirmed" if observed else "refused"
    return broker._record(
        receipt.role, receipt.op, receipt.pr, receipt.expected_head, state,
        receipt.request_hash, detail=note or "reconciled by observation", op_id=receipt.op_id,
    )


class CountingTransport:
    """Records what it was asked to send and never reaches the network. The count is the evidence."""

    def __init__(self, response: tuple[int, dict] | None = None, error: Exception | None = None):
        self.requests: list[tuple[str, dict, dict[str, str]]] = []
        self._response = response or (200, {"id": 1, "state": "COMMENTED"})
        self._error = error

    def post(self, url: str, body: dict, headers: dict[str, str]) -> tuple[int, dict]:
        self.requests.append((url, body, headers))
        if self._error is not None:
            raise self._error
        return self._response

    def delete(self, url: str, headers: dict[str, str]) -> tuple[int, dict]:
        self.requests.append((url, {}, headers))
        if self._error is not None:
            raise self._error
        return self._response

    @property
    def count(self) -> int:
        return len(self.requests)


def quote_path(path: str) -> str:
    return urllib.parse.quote(path, safe="/")
