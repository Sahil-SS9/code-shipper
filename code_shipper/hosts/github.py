"""Read-only GitHub adapter, and a recorded-response fake for tests.

Reads only. There is no method here that creates, edits, approves or merges anything, and no token is
read from the environment: the caller supplies it, so the adapter never discovers a credential on its
own.

Every request goes through one `_get` so that the timeout, the user agent, the redirect policy and
the error mapping are in one place. Redirects are refused: a 3xx that carried the auth header to
another host would be a credential leak, which is the same defect already fixed in the broker.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .base import (
    CheckResult,
    Comment,
    Diff,
    FilePatch,
    HostError,
    PullRequest,
    Review,
    split_slug,
)

API_ROOT = "https://api.github.com"
USER_AGENT = "code-shipper/0.1"
DEFAULT_TIMEOUT = 20.0


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect must not carry the Authorization header to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        raise HostError(f"redirect refused: {code}", status=code)


class GitHubHost:
    """Read-only GitHub reads over REST."""

    name = "github"

    def __init__(
        self,
        token: str,
        *,
        api_root: str = API_ROOT,
        timeout: float = DEFAULT_TIMEOUT,
        opener: Any | None = None,
    ) -> None:
        if not isinstance(token, str) or not token:
            raise HostError("a token is required for GitHub reads")
        self._token = token
        self._api = api_root.rstrip("/")
        self._timeout = timeout
        self._opener = opener or urllib.request.build_opener(_RefuseRedirects)

    def __repr__(self) -> str:
        return f"GitHubHost(api_root={self._api!r}, token=<redacted>)"

    # -- transport ---------------------------------------------------------

    def _get_log(self, path: str) -> str:
        """A plain-text endpoint (job logs). Separate from _get because the response is not JSON."""
        url = f"{self._api}{path}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "text/plain",
                "User-Agent": USER_AGENT,
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            # A missing or expired log is not worth failing a review over: the review proceeds
            # without the hint, which is the same as having no failing checks.
            if exc.code in (404, 410, 403):
                return ""
            raise HostError(f"HTTP {exc.code} reading a log") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            return ""

    def _get(self, path: str, *, accept: str = "application/vnd.github+json") -> tuple[Any, dict]:
        url = f"{self._api}{path}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": accept,
                "User-Agent": USER_AGENT,
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read()
                headers = dict(response.headers)
        except HostError:
            raise
        except urllib.error.HTTPError as exc:
            status = exc.code
            retryable = status >= 500 or status == 429
            raise HostError(f"GET {path} failed: {status}", status=status, retryable=retryable) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HostError(f"GET {path} failed: {exc}", retryable=True) from None
        if accept != "application/vnd.github+json":
            return raw, headers
        try:
            return json.loads(raw or b"null"), headers
        except json.JSONDecodeError:
            raise HostError(f"GET {path} returned malformed JSON") from None

    # -- reads -------------------------------------------------------------

    def get_pr(self, repo: str, number: int) -> PullRequest:
        owner, name = split_slug(repo)
        data, _ = self._get(f"/repos/{owner}/{name}/pulls/{int(number)}")
        return _to_pr(data)

    def list_open_prs(self, repo: str, updated_since: str | None = None) -> list[PullRequest]:
        owner, name = split_slug(repo)
        query = "?state=open&per_page=100"
        if updated_since:
            query += f"&sort=updated&direction=desc"
        data, _ = self._get(f"/repos/{owner}/{name}/pulls{query}")
        if not isinstance(data, list):
            raise HostError("unexpected payload for PR list")
        return [_to_pr(entry) for entry in data]

    def compare(self, repo: str, base: str, head: str) -> Diff:
        """The diff between two commits — used to scope an incremental review (D11).

        `base` is the last reviewed head and `head` is the new one, so the result says which files the
        new commits touched. Content for the review still comes from `get_diff`, because a hunk shown
        against a moved base loses the context the reviewer needs.
        """
        owner, name = split_slug(repo)
        data, _ = self._get(f"/repos/{owner}/{name}/compare/{base}...{head}")
        if not isinstance(data, dict):
            raise HostError("unexpected payload for compare")
        patches: list[FilePatch] = []
        for entry in data.get("files") or []:
            if not isinstance(entry, dict):
                continue
            patches.append(
                FilePatch(
                    path=str(entry.get("filename", "")),
                    status=str(entry.get("status", "modified")),
                    old_path=entry.get("previous_filename"),
                    patch=str(entry.get("patch", "")),
                )
            )
        return Diff(files=tuple(patches))

    def failing_log(self, repo: str, head_sha: str, *, max_chars: int = 20_000) -> str:
        """The tail of the failing check's log for this commit, or "" when nothing failed (C10).

        Returned as raw text for the caller to frame as untrusted. This deliberately does not parse or
        filter: the log is author-influenced output, so the value is in *seeing* it labelled as a hint,
        not in this method deciding what it means.
        """
        owner, name = split_slug(repo)
        runs, _ = self._get(
            f"/repos/{owner}/{name}/actions/runs?head_sha={head_sha}&per_page=10"
        )
        for run in (runs or {}).get("workflow_runs") or []:
            if str(run.get("conclusion")) not in ("failure", "timed_out", "cancelled"):
                continue
            jobs, _ = self._get(
                f"/repos/{owner}/{name}/actions/runs/{run.get('id')}/jobs?per_page=20"
            )
            for job in (jobs or {}).get("jobs") or []:
                if str(job.get("conclusion")) not in ("failure", "timed_out"):
                    continue
                text = self._get_log(f"/repos/{owner}/{name}/actions/jobs/{job.get('id')}/logs")
                if text:
                    return text[-max_chars:]
        return ""

    def annotations(self, repo: str, head_sha: str) -> list[str]:
        """Check annotations for this commit, as path hints only."""
        owner, name = split_slug(repo)
        runs, _ = self._get(f"/repos/{owner}/{name}/check-runs?head_sha={head_sha}")
        hints: list[str] = []
        for run in (runs or {}).get("check_runs") or []:
            for annotation in run.get("output", {}).get("annotations") or []:
                path = annotation.get("path")
                if isinstance(path, str) and path and path not in hints:
                    hints.append(path)
        return hints

    def get_diff(self, repo: str, number: int) -> Diff:
        owner, name = split_slug(repo)
        data, _ = self._get(f"/repos/{owner}/{name}/pulls/{int(number)}/files?per_page=100")
        if not isinstance(data, list):
            raise HostError("unexpected payload for PR files")
        files = tuple(
            FilePatch(
                path=str(entry.get("filename", "")),
                status=str(entry.get("status", "modified")),
                old_path=entry.get("previous_filename"),
                patch=str(entry.get("patch") or ""),
            )
            for entry in data
            if isinstance(entry, dict)
        )
        return Diff(files=files)

    def get_checks(self, repo: str, sha: str) -> list[CheckResult]:
        owner, name = split_slug(repo)
        results: list[CheckResult] = []

        data, _ = self._get(f"/repos/{owner}/{name}/commits/{sha}/check-runs")
        for entry in (data or {}).get("check_runs", []) if isinstance(data, dict) else []:
            results.append(
                CheckResult(
                    name=str(entry.get("name", "")),
                    status=str(entry.get("status", "queued")),
                    conclusion=entry.get("conclusion"),
                    details_url=entry.get("details_url"),
                    check_id=entry.get("id"),
                )
            )

        status_data, _ = self._get(f"/repos/{owner}/{name}/commits/{sha}/status")
        for entry in (status_data or {}).get("statuses", []) if isinstance(status_data, dict) else []:
            state = str(entry.get("state", ""))
            results.append(
                CheckResult(
                    name=str(entry.get("context", "")),
                    status="completed",
                    conclusion="success" if state == "success" else state,
                    details_url=entry.get("target_url"),
                )
            )
        return results

    def get_check_log(self, repo: str, check: CheckResult) -> str:
        """Return the tail of a check's log.

        GitHub does not serve check-run logs over REST for arbitrary apps; this returns the detail
        URL content when it is a GitHub-owned job log and an empty string otherwise, so a caller
        gets an honest empty result rather than an invented one.
        """
        if not check.details_url:
            return ""
        url = check.details_url
        if not url.startswith(("https://github.com/", f"{self._api}/")):
            return ""
        try:
            raw, _ = self._get(urllib.parse.urlparse(url).path, accept="text/plain")
        except HostError:
            return ""
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        return "\n".join(text.splitlines()[-200:])

    def list_comments(self, repo: str, number: int, since: str | None = None) -> list[Comment]:
        owner, name = split_slug(repo)
        query = "?per_page=100"
        if since:
            query += f"&since={urllib.parse.quote(since)}"
        data, _ = self._get(f"/repos/{owner}/{name}/issues/{int(number)}/comments{query}")
        if not isinstance(data, list):
            raise HostError("unexpected payload for comments")
        return [
            Comment(
                id=int(entry.get("id", 0)),
                author=str((entry.get("user") or {}).get("login", "")),
                body=str(entry.get("body", "")),
                created_at=str(entry.get("created_at", "")),
            )
            for entry in data
            if isinstance(entry, dict)
        ]

    def list_reviews(self, repo: str, number: int) -> list[Review]:
        owner, name = split_slug(repo)
        data, _ = self._get(f"/repos/{owner}/{name}/pulls/{int(number)}/reviews?per_page=100")
        if not isinstance(data, list):
            raise HostError("unexpected payload for reviews")
        return [
            Review(
                id=int(entry.get("id", 0)),
                author=str((entry.get("user") or {}).get("login", "")),
                state=str(entry.get("state", "")),
                body=str(entry.get("body") or ""),
            )
            for entry in data
            if isinstance(entry, dict)
        ]

    def list_labels(self, repo: str, number: int) -> list[str]:
        """The label names on a PR. Read-only, so it belongs on the read host."""
        owner, name = split_slug(repo)
        data, _ = self._get(f"/repos/{owner}/{name}/issues/{int(number)}/labels")
        if not isinstance(data, list):
            raise HostError("unexpected payload for labels")
        return [str(entry.get("name", "")) for entry in data if isinstance(entry, dict)]

    def permission_of(self, repo: str, login: str) -> str:
        owner, name = split_slug(repo)
        try:
            data, _ = self._get(f"/repos/{owner}/{name}/collaborators/{login}/permission")
        except HostError as exc:
            if exc.status == 404:
                return "none"
            raise
        if not isinstance(data, dict):
            return "none"
        return str(data.get("permission", "none"))

    def read_file(self, repo: str, ref: str, path: str) -> bytes | None:
        owner, name = split_slug(repo)
        quoted = urllib.parse.quote(path, safe="/")
        try:
            raw, headers = self._get(
                f"/repos/{owner}/{name}/contents/{quoted}?ref={urllib.parse.quote(ref)}",
                accept="application/vnd.github.raw",
            )
        except HostError as exc:
            if exc.status == 404:
                return None
            raise
        return raw if isinstance(raw, bytes) else str(raw).encode()

    def default_branch(self, repo: str) -> str:
        owner, name = split_slug(repo)
        data, _ = self._get(f"/repos/{owner}/{name}")
        if not isinstance(data, dict):
            raise HostError("unexpected payload for repository")
        return str(data.get("default_branch", "main"))


def _to_pr(data: Any) -> PullRequest:
    if not isinstance(data, dict):
        raise HostError("unexpected payload for a pull request")
    head = data.get("head") or {}
    base = data.get("base") or {}
    repo_full = (head.get("repo") or {}).get("full_name") if isinstance(head, dict) else None
    base_full = (base.get("repo") or {}).get("full_name") if isinstance(base, dict) else None
    return PullRequest(
        number=int(data.get("number", 0)),
        title=str(data.get("title", "")),
        author=str((data.get("user") or {}).get("login", "")),
        head_sha=str(head.get("sha", "")),
        head_ref=str(head.get("ref", "")),
        base_ref=str(base.get("ref", "")),
        draft=bool(data.get("draft", False)),
        from_fork=bool(repo_full and base_full and repo_full != base_full),
        labels=tuple(
            str((label or {}).get("name", ""))
            for label in data.get("labels") or []
            if isinstance(label, dict)
        ),
        state=str(data.get("state", "open")),
        body=str(data.get("body") or ""),
    )


class RecordedHost:
    """A CodeHost that answers from recorded payloads and never touches the network.

    Used for T-CONTRACT-02's offline half. Unknown calls raise rather than returning empty, so a test
    cannot pass by accident on a missing fixture.
    """

    name = "recorded"

    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, tuple, tuple]] = []

    def _answer(self, key: str, *args, **kwargs):
        self.calls.append((key, args, tuple(sorted(kwargs.items()))))
        if key not in self._responses:
            raise HostError(f"no recorded response for {key!r}")
        return self._responses[key]

    def get_pr(self, repo: str, number: int) -> PullRequest:
        return self._answer("get_pr", repo, number)

    def list_open_prs(self, repo: str, updated_since: str | None = None) -> list[PullRequest]:
        return self._answer("list_open_prs", repo, updated_since)

    def get_diff(self, repo: str, number: int) -> Diff:
        return self._answer("get_diff", repo, number)

    def get_checks(self, repo: str, sha: str) -> list[CheckResult]:
        return self._answer("get_checks", repo, sha)

    def get_check_log(self, repo: str, check: CheckResult) -> str:
        return self._answer("get_check_log", repo, check)

    def list_comments(self, repo: str, number: int, since: str | None = None) -> list[Comment]:
        return self._answer("list_comments", repo, number, since)

    def list_reviews(self, repo: str, number: int) -> list[Review]:
        return self._answer("list_reviews", repo, number)

    def permission_of(self, repo: str, login: str) -> str:
        return self._answer("permission_of", repo, login)

    def read_file(self, repo: str, ref: str, path: str) -> bytes | None:
        return self._answer("read_file", repo, ref, path)

    def default_branch(self, repo: str) -> str:
        return self._answer("default_branch", repo)
