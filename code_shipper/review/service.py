"""The review a caller actually runs: filters, cache, incremental scope, profile, cap, state.

Everything the honesty pack built is wired here, because a component that is tested but never called
is not a feature. The order is deliberate:

1. **Cap and pause** — before any model call, so a capped PR costs nothing.
2. **Cache** — keyed on the head, so the same head twice is one review and zero second calls.
3. **Path filters** — lockfiles and generated output are skipped and *named*, so "no findings" cannot
   quietly mean "nothing was read".
4. **Incremental scope** — only files touched since the last completed review.
5. The lens run, filters already applied.
6. **State advance** — only on a complete review, and the union rule keeps unfixed important findings
   alive across pushes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..hosts.base import Diff
from .cache import ReviewCache
from .engine import ReviewOutcome, review as run_review
from .filters import PathFilter, summarise_skipped
from .incremental import Scoped, restrict
from .state import ReviewState, ReviewStore, advance, pause

# Volume profiles (D10). Orthogonal to depth: a quiet deep review and an assertive quick review are
# both meaningful, and neither changes whether verification runs.
PROFILES: dict[str, dict[str, bool]] = {
    "quiet": {"post_nits": False, "raise_bar": False},
    "standard": {"post_nits": True, "raise_bar": False},
    "assertive": {"post_nits": True, "raise_bar": True},
}


@dataclass
class ReviewRequest:
    repo: str
    pr: int
    head_sha: str
    diff: Diff
    since_diff: Diff | None = None
    # D5: what the host says about the relationship between last_reviewed_head and this head. Either
    # the compare payload ({"status", "files"}) or just the status string. Absent means "unknown", and
    # unknown ancestry is treated as a fallback to a full review rather than assumed safe.
    compare: dict[str, Any] | None = None
    compare_status: str | None = None
    level: str = "standard"
    profile: str = "standard"
    trigger: str = "manual"
    intent: str = ""
    log_tail: str = ""
    workdir: Any = None
    env: dict[str, str] | None = None
    budget: Any = None
    # Phase 4b. All off by default: risk is advisory, detection is opt-in, memory is shadow.
    risk_score: bool = True
    effort_detection: bool = False
    memory_mode: str = "shadow"
    commit_message: str = ""
    lines_changed: int = 0
    touches_tests: bool | None = None


@dataclass
class ReviewResult:
    outcome: ReviewOutcome
    state: ReviewState
    cached: bool = False
    paused: bool = False
    pause_reason: str = ""
    incremental: bool = False
    scope: Scoped | None = None
    ignored_files: tuple[str, ...] = ()
    ignored_summary: str = ""
    model_calls: int = 0
    profile: str = "standard"
    coverage: dict[str, Any] = field(default_factory=dict)
    union_important: tuple[str, ...] = ()
    guard_note: str = ""
    risk: dict[str, Any] = field(default_factory=dict)
    effort: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = self.outcome.as_dict()
        payload.update(
            {
                "cached": self.cached,
                "paused": self.paused,
                "pause_reason": self.pause_reason,
                "incremental": self.incremental,
                "profile": self.profile,
                "ignored_files": list(self.ignored_files),
                "ignored_summary": self.ignored_summary,
                "model_calls": self.model_calls,
                "coverage": self.coverage,
                "union_important": list(self.union_important),
                "guard_note": self.guard_note,
                "risk": dict(self.risk),
                "effort": dict(self.effort),
            }
        )
        if self.scope is not None:
            payload["scope"] = {
                "included": list(self.scope.included),
                "excluded": list(self.scope.excluded),
                "since": self.scope.since,
                "summary": self.scope.summary(),
            }
        return payload


def _coverage(diff: Diff, ignored: list[str], findings: list[Any]) -> dict[str, Any]:
    """What was looked at, per file, and what it produced (C9).

    A file with no findings is a statement worth making explicitly: it is what separates "reviewed and
    clean" from "never opened".
    """
    with_findings = {getattr(f, "path", "") for f in findings}
    files = [patch.path for patch in diff.files]
    return {
        "files_reviewed": len(files),
        "files_with_findings": len([f for f in files if f in with_findings]),
        "files_clean": len([f for f in files if f not in with_findings]),
        "files_ignored": len(ignored),
        "reviewed": files,
        "clean": [f for f in files if f not in with_findings],
    }


def run(
    request: ReviewRequest,
    *,
    harness: Any,
    store: ReviewStore | None = None,
    cache: ReviewCache | None = None,
    path_filter: PathFilter | None = None,
    max_reviews_per_pr: int = 20,
    clock: Callable[[], float] | None = None,
) -> ReviewResult:
    """One review, with every component wired in the order the docstring gives."""
    path_filter = path_filter or PathFilter()
    state = store.load(request.repo, request.pr) if store is not None else ReviewState(
        repo=request.repo, pr=request.pr
    )
    profile = request.profile if request.profile in PROFILES else "standard"

    if state.paused:
        return ReviewResult(
            outcome=ReviewOutcome(level=request.level, complete=False,
                                  errors=[f"paused: {state.pause_reason}"]),
            state=state,
            paused=True,
            pause_reason=state.pause_reason,
            profile=profile,
        )

    # The cap is checked before any model call: a capped PR must cost nothing.
    if state.reviews >= max_reviews_per_pr:
        reason = (
            f"review cap reached: {state.reviews} of {max_reviews_per_pr} reviews on this PR. "
            "Push a fix or raise the cap to continue."
        )
        state = pause(state, reason)
        if store is not None:
            store.save(state)
        return ReviewResult(
            outcome=ReviewOutcome(level=request.level, complete=False, errors=[reason]),
            state=state,
            paused=True,
            pause_reason=reason,
            profile=profile,
        )

    # Cache on the head: the same head twice must not cost a second model call.
    if cache is not None:
        hit = cache.get(request.repo, request.head_sha)
        if hit is not None:
            outcome = ReviewOutcome(
                level=hit.get("level", request.level),
                lenses_run=list(hit.get("lenses_run") or []),
                raw_findings=hit.get("raw_findings", 0),
                confirmed=hit.get("confirmed", 0),
                dropped_unverified=hit.get("dropped_unverified", 0),
                complete=hit.get("complete", True),
                errors=list(hit.get("errors") or []),
            )
            return ReviewResult(
                outcome=outcome, state=state, cached=True, profile=profile,
                union_important=tuple(sorted(state.important_fingerprints())),
            )

    # Path filters: skip lockfiles and generated output, and name what was skipped.
    reviewed_paths, ignored = path_filter.partition([p.path for p in request.diff.files])
    kept = tuple(p for p in request.diff.files if p.path in set(reviewed_paths))
    diff = Diff(files=kept)

    # Incremental scope: only the files touched since the last completed review.
    #
    # D5 guard first. Incremental review assumes the new head descends from the last reviewed one; a
    # force-push, a rebase or a base change breaks that, and the input still *looks* valid. Both of the
    # cases below used to be trusted: a replaced history was diffed as if it were a continuation, and an
    # empty comparison returned a "complete" result with no lenses run — the most reassuring possible
    # output for a tree nothing had looked at. Now either case falls back to a full review, and the
    # body says so.
    scope: Scoped | None = None
    incremental = False
    guard = None
    if request.since_diff is not None and state.is_incremental(request.head_sha):
        from . import ancestry

        compare_payload = None
        if request.compare is not None:
            compare_payload = request.compare
        elif request.compare_status is not None:
            compare_payload = {"status": request.compare_status,
                               "files": list(request.diff.files)}
        else:
            compare_payload = {"status": "", "files": None}

        guard = ancestry.check(
            last_reviewed_head=state.last_reviewed_head,
            new_head=request.head_sha,
            compare=compare_payload,
        )
        if guard.fell_back:
            # Deliberately no `scope`: a full review of the whole diff, with the reason in the body.
            incremental = False
        else:
            scope = restrict(diff, request.since_diff, since_sha=state.last_reviewed_head,
                             to_sha=request.head_sha)
            if scope.empty:
                # Head moved, nothing scoped to review: treated as a full review, never as "nothing
                # to do". An empty incremental pass is not evidence that the tree is unchanged.
                guard = ancestry.GuardResult(
                    False, ancestry.EMPTY_WITH_MOVED_HEAD,
                    "the incremental scope covered no files",
                )
                scope = None
                incremental = False
            else:
                diff = scope.diff
                incremental = True

    outcome = run_review(
        diff=diff,
        harness=harness,
        level=request.level,
        workdir=request.workdir,
        env=request.env,
        budget=request.budget,
        log_tail=request.log_tail,
        intent=request.intent,
        assertiveness=profile,
    )

    # Volume profile: quiet drops nits, assertive is the caller's business at the lens level. Neither
    # touches whether verification ran — that is depth's job, and depth already ran it.
    if not PROFILES[profile]["post_nits"] and outcome.plan is not None:
        outcome.plan = outcome.plan.without_nits()

    if cache is not None and outcome.complete:
        cache.put(request.repo, request.head_sha, outcome.as_dict())

    state = advance(
        state,
        head_sha=request.head_sha,
        complete=outcome.complete,
        findings=outcome.findings,
        covered_paths=[patch.path for patch in diff.files],
        now=clock() if clock else None,
    )
    if store is not None:
        store.save(state)

    # Phase 4b. The risk score is advisory: it is computed for the summary and is not passed to any
    # approval decision, and cannot be — the evaluator takes only a policy and facts.
    risk_payload: dict[str, Any] = {}
    if request.risk_score:
        from .risk import score as risk_score

        risk_payload = risk_score(
            changed_files=[patch.path for patch in request.diff.files],
            lines_changed=request.lines_changed,
            findings=outcome.findings,
            touches_tests=request.touches_tests,
        ).as_dict()

    effort_payload: dict[str, Any] = {}
    if request.effort_detection:
        from .effort import detect

        effort_payload = detect(
            enabled=True,
            patch="\n".join(patch.patch for patch in request.diff.files),
            commit_message=request.commit_message,
            lines_changed=request.lines_changed,
            touches_tests=request.touches_tests,
        ).as_dict()

    return ReviewResult(
        outcome=outcome,
        state=state,
        incremental=incremental,
        scope=scope,
        ignored_files=tuple(ignored),
        ignored_summary=summarise_skipped(ignored),
        model_calls=len(outcome.lenses_run) + (1 if outcome.raw_findings else 0),
        profile=profile,
        coverage=_coverage(diff, ignored, outcome.findings),
        union_important=tuple(sorted(state.important_fingerprints())),
        guard_note=guard.body_note() if guard is not None else "",
        risk=risk_payload,
        effort=effort_payload,
    )
