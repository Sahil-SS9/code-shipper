"""Classify a work item: what, if anything, should the steward do about this PR?

FR-S1 lists the four things a fix is allowed to work on: failing checks whose cause is in code the PR
touches, merge conflicts, human review comments, and Important findings. Everything else is not a
work item, and saying so plainly is part of the job — a steward that silently does nothing is
indistinguishable from one that is broken.

The classification is deliberately conservative about *cause*. "CI failed" is not the same as "CI
failed because of this PR": a red check on a dependency, or a flaky test in untouched code, is not a
fix the steward should attempt, and attempting it produces a confident wrong diff. So a failure is
only in scope when the failing job's log or path set intersects the PR's changed files.

No model call happens here. This is a decision over facts the host already has.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


class WorkKind:
    """The kinds of work FR-S1 permits. Anything not in here is not a work item."""

    FAILING_CHECK = "failing_check"
    CONFLICT = "conflict"
    REVIEW_COMMENT = "review_comment"
    IMPORTANT_FINDING = "important_finding"
    NONE = "none"


# Kinds a v1 fix may attempt. Kept as a set so adding a kind is an explicit act.
ACTIONABLE = frozenset(
    {WorkKind.FAILING_CHECK, WorkKind.CONFLICT, WorkKind.REVIEW_COMMENT, WorkKind.IMPORTANT_FINDING}
)

# A review comment asking for an explanation rather than a change. FR-S1 says reply, don't rewrite.
QUESTION_MARKERS = re.compile(
    r"(\?\s*$|^(why|what|how|which|where|who|when)\b|\bwhy\b|\bexplain\b|\brationale\b)",
    re.IGNORECASE,
)
# A comment that is clearly a design-level request rather than a small change.
LARGE_REQUEST_MARKERS = re.compile(
    r"\b(refactor|redesign|restructure|rewrite|overhaul|architect\w*|rethink|split (this|the) "
    r"(file|module|class))\b",
    re.IGNORECASE,
)


@dataclass
class WorkItem:
    """One thing to do, with the evidence for doing it."""

    kind: str
    reason: str
    paths: list[str] = field(default_factory=list)
    source_id: str = ""
    reply_only: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        return self.kind in ACTIONABLE and not self.reply_only

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reason": self.reason,
            "paths": self.paths,
            "source_id": self.source_id,
            "reply_only": self.reply_only,
            "actionable": self.actionable,
        }


def _norm(path: str) -> str:
    from .policy import normalise_path

    return normalise_path(path)


def _touches(changed_files: list[str], path: str) -> bool:
    target = _norm(path)
    for changed in changed_files:
        normalised = _norm(changed)
        if target == normalised:
            return True
        # a log line naming a directory covers the files under it
        if normalised.startswith(target.rstrip("/") + "/"):
            return True
    return False


# A path as it appears in a CI log: word-bounded, with a known source extension, optionally with a
# line number or `::` test selector after it.
_PATH_IN_LOG = re.compile(
    r"\b([\w./-]+\.(?:py|pyi|js|jsx|ts|tsx|go|rs|java|rb|php|cs|c|cc|cpp|h|hpp|sh))(?::\d+)?\b"
)

# A path that identifies a test, by convention. Same conventions the policy uses.
_TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__)/|(^|/)test_[^/]+$|_test\.\w+$|\.test\.\w+$|(^|/)conftest\.py$"
)


def _looks_like_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(_norm(path)))


def _dedupe(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for path in paths:
        if path and path not in seen:
            seen.add(path)
            out.append(path)
    return out


def _module_names(path: str) -> set[str]:
    """The import names a file could be referred to by: `calc/__init__.py` -> {"calc", "calc.__init__"}."""
    normalised = _norm(path)
    if normalised.endswith(".py"):
        normalised = normalised[:-3]
    parts = [p for p in normalised.split("/") if p and p != "."]
    if not parts:
        return set()
    names = {parts[-1], ".".join(parts)}
    if parts[-1] == "__init__" and len(parts) > 1:
        names |= {parts[-2], ".".join(parts[:-1])}
    return {n for n in names if n}


def _imports_changed(test_path: str, imports: list[str], changed_files: list[str]) -> bool:
    """Whether a test file imports any module this PR changed.

    This is the evidence that separates "a test broke because of this change" from "a test broke".
    Measured on sandbox PR #23: the log names only `tests/test_calc.py`, and the changed file is
    `calc/__init__.py`; the test's `from calc import divide` is what makes the failure ours. Without
    that link, a log-named test file is not evidence of anything.
    """
    changed_names: set[str] = set()
    for changed in changed_files:
        changed_names |= _module_names(changed)
    for imported in imports or []:
        root = imported.split(".")[0]
        if imported in changed_names or root in changed_names:
            return True
    return False


def imports_of(log: str) -> list[str]:
    """The test files the log names. The caller resolves each to its real imports from the checkout.

    Returning a path rather than guessing at import names keeps this honest: only the file on disk
    knows what it imports.
    """
    found: list[str] = []
    for line in (log or "").splitlines():
        for token in _PATH_IN_LOG.findall(line):
            if _looks_like_test_path(token) and token not in found:
                found.append(token)
    return found


def parse_imports(source: str) -> list[str]:
    """Module names a Python file imports, for the import-link check. Never raises."""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
            modules.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def classify_failing_check(
    check: Any, changed_files: list[str], *, max_files: int = 20
) -> WorkItem:
    """A failing check is in scope only when it points at code this PR touches.

    ``check`` needs ``name``, ``conclusion``/``state`` and optionally ``log_tail`` and ``files``.

    **The failing test file itself counts, and that is not a loophole.** A PR typically changes source
    and leaves the test alone, so a failure reported at `tests/test_calc.py:11` names a file the PR did
    not touch — while the cause is plainly in the changed `calc/__init__.py`. Measured on sandbox PR
    #23: the only changed file was `calc/__init__.py`, the assertion failed in `tests/test_calc.py`, and
    a changed-files-only rule refused the run with "the cause is probably outside this change". That
    was wrong, so a log-named path is accepted when it either *is* a changed file or *imports around*
    one — see `_related_to_change`.
    """
    name = getattr(check, "name", "") or "unnamed check"
    conclusion = (getattr(check, "conclusion", "") or getattr(check, "state", "") or "").lower()
    if conclusion in ("success", "neutral", "skipped", ""):
        return WorkItem(WorkKind.NONE, f"check {name!r} is not failing ({conclusion or 'unknown'})")

    log = getattr(check, "log_tail", "") or ""
    named: list[str] = [f for f in (getattr(check, "files", None) or []) if isinstance(f, str)]

    # Files the log names outright, plus files the check reports.
    in_log: list[str] = []
    for line in log.splitlines():
        for token in _PATH_IN_LOG.findall(line):
            candidate = _norm(token)
            if candidate and candidate not in in_log:
                in_log.append(candidate)

    reported = [_norm(p) for p in named] + in_log
    changed_norm = {_norm(c) for c in changed_files}
    direct = [p for p in reported if p in changed_norm or _touches(changed_files, p)]

    # A log-named test file is evidence only when it imports a module this PR changed. Without that
    # link, any unrelated failing test would look like our problem.
    imports = list(getattr(check, "test_imports", None) or [])
    related: list[str] = []
    for path in reported:
        if path in direct or not _looks_like_test_path(path):
            continue
        if _imports_changed(path, imports, changed_files):
            related.append(path)
    candidates = _dedupe(direct + related)

    if not candidates:
        return WorkItem(
            WorkKind.NONE,
            f"check {name!r} is failing but nothing in its output points at a file this PR changed; "
            "the cause is probably outside this change",
        )

    reason = (
        f"check {name!r} failed and its output points at {len(candidates)} file(s) related to this "
        "change"
    )
    if related and not direct:
        reason = (
            f"check {name!r} failed in {related[0]!r}, a test file that exercises code this PR changes; "
            "the failure is caused by this change"
        )
    return WorkItem(
        WorkKind.FAILING_CHECK,
        reason,
        paths=candidates[:max_files],
        source_id=name,
        detail={"conclusion": conclusion, "related_tests": related},
    )


def classify_conflict(*, pr: Any, mergeable_state: str) -> WorkItem:
    """A merge conflict the head branch can resolve."""
    if (mergeable_state or "").lower() not in ("dirty", "behind", "conflicted"):
        return WorkItem(WorkKind.NONE, f"no merge conflict ({mergeable_state or 'unknown'})")
    return WorkItem(
        WorkKind.CONFLICT,
        "the PR has a merge conflict that can be resolved by merging the base branch in",
        source_id=str(getattr(pr, "number", "")),
        detail={"state": mergeable_state},
    )


def classify_review_comment(comment: Any, changed_files: list[str]) -> WorkItem:
    """A human review comment: a small change, a reply, or too large to attempt.

    The third case is the one worth having. A comment asking for a redesign is not a fix, and
    attempting it produces a large unreviewable diff — worse than a reply saying so.
    """
    body = (getattr(comment, "body", "") or "").strip()
    path = getattr(comment, "path", "") or ""
    ident = str(getattr(comment, "id", "") or "")

    if not body:
        return WorkItem(WorkKind.NONE, "empty comment")

    if LARGE_REQUEST_MARKERS.search(body):
        return WorkItem(
            WorkKind.REVIEW_COMMENT,
            "the comment asks for a design-level change, which is a reply rather than a fix",
            paths=[_norm(path)] if path else [],
            source_id=ident,
            reply_only=True,
        )

    if QUESTION_MARKERS.search(body) and not _looks_like_a_directive(body):
        return WorkItem(
            WorkKind.REVIEW_COMMENT,
            "the comment asks a question, so it needs an answer rather than a code change",
            paths=[_norm(path)] if path else [],
            source_id=ident,
            reply_only=True,
        )

    paths = [_norm(path)] if path and _touches(changed_files, path) else []
    if path and not paths:
        return WorkItem(
            WorkKind.NONE,
            f"the comment is on {path!r}, which this PR does not change",
            source_id=ident,
        )

    return WorkItem(
        WorkKind.REVIEW_COMMENT,
        "a specific change was requested on a file this PR changes",
        paths=paths,
        source_id=ident,
    )


def _looks_like_a_directive(body: str) -> bool:
    """A question that is really an instruction: "why not just clamp it?" is a change request."""
    return bool(re.search(r"\b(please|could you|can you|should|instead|let's|lets|just)\b",
                          body, re.IGNORECASE))


def classify_finding(finding: Any) -> WorkItem:
    """An Important finding is work; a nit is not."""
    severity = (getattr(finding, "severity", "") or "").lower()
    title = getattr(finding, "title", "") or ""
    if severity not in ("important", "blocking"):
        return WorkItem(WorkKind.NONE, f"finding severity {severity or 'unknown'} is not important")
    return WorkItem(
        WorkKind.IMPORTANT_FINDING,
        f"an {severity} finding: {title}",
        paths=[_norm(getattr(finding, "path", ""))],
        source_id=getattr(finding, "fingerprint", "") or getattr(finding, "id", ""),
    )


def classify(
    *,
    pr: Any = None,
    mergeable_state: str = "",
    checks: list[Any] | None = None,
    comments: list[Any] | None = None,
    findings: list[Any] | None = None,
    changed_files: list[str] | None = None,
) -> list[WorkItem]:
    """All work items for this PR, most specific first.

    Returns an empty list when there is nothing to do — and the caller must say so rather than being
    silent, which is why `WorkKind.NONE` items are included rather than filtered out here.
    """
    files = list(changed_files or [])
    items: list[WorkItem] = []

    items.append(classify_conflict(pr=pr, mergeable_state=mergeable_state))
    for check in checks or []:
        items.append(classify_failing_check(check, files))
    for finding in findings or []:
        items.append(classify_finding(finding))
    for comment in comments or []:
        items.append(classify_review_comment(comment, files))

    if not any(item.kind != WorkKind.NONE for item in items):
        items.append(WorkItem(WorkKind.NONE, "nothing to do: no failing check caused by this change, "
                                             "no conflict, no important finding, no change request"))
    return items


def actionable(items: list[WorkItem]) -> list[WorkItem]:
    """The items a fix may act on. Reply-only and none are excluded, not deleted."""
    return [item for item in items if item.actionable]
