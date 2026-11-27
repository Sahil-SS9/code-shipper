"""Ranking and the posting plan.

Ordering is by severity first, then by file and line, so a reviewer reading the posted review sees the
serious things first and can stop early. The cap is deliberate (15 Important, 10 Nit per §3): past
that, a review stops being read, and the remainder is summarised instead of dumped.

De-duplication matters more than it looks: five lenses over one change routinely find the same line
from different angles, and posting it five times is noise that hides the other findings.
"""

from __future__ import annotations

from dataclasses import dataclass

from .verify import Finding

MAX_IMPORTANT = 15
MAX_NIT = 10
SEVERITY_ORDER = {"important": 0, "nit": 1, "pre_existing": 2}


# Kept here rather than imported from engine: rank is imported by engine, and a cycle would be worse than
# one repeated string constant guarded by a test that the two agree.
SECURITY_LENS = "security"


@dataclass(frozen=True)
class PostingPlan:
    to_post: list[Finding]
    omitted: int
    omitted_important: int
    omitted_nit: int

    def without_nits(self) -> "PostingPlan":
        """The same plan with nits dropped, counted rather than discarded silently.

        A quiet profile hides nits from the posted review; the count still goes into the body so a
        reader knows something was found and is not being shown.
        """
        # A security finding is never dropped as a nit, whatever severity the lens gave it. The quiet
        # profile exists to cut noise; a vulnerability is not noise, and the lens that finds it is the one
        # whose findings this product must not hide.
        kept = [
            f for f in self.to_post
            if (getattr(f, "severity", "") or "").lower() != "nit"
            or getattr(f, "lens", "") == SECURITY_LENS
        ]
        dropped = len(self.to_post) - len(kept)
        return PostingPlan(
            to_post=kept,
            omitted=self.omitted + dropped,
            omitted_important=self.omitted_important,
            omitted_nit=self.omitted_nit + dropped,
        )

    def summary_line(self) -> str:
        if not self.omitted:
            return ""
        parts = []
        if self.omitted_important:
            parts.append(f"{self.omitted_important} more important")
        if self.omitted_nit:
            parts.append(f"{self.omitted_nit} more minor")
        return (
            f"{self.omitted} further finding(s) not shown inline: " + ", ".join(parts) + "."
        )


def dedupe(findings: list[Finding]) -> list[Finding]:
    """Drop findings that point at the same place with the same claim.

    Keyed on path, line and a *normalised* title. Two lenses routinely describe one bug in different
    words ("concatenates string forms instead of adding" vs "concatenates strings instead of adding
    numbers"), so an exact-title match misses them and the reviewer sees the same problem twice.

    The normalisation is deliberately crude: lowercase, strip punctuation, collapse whitespace, and
    keep only the leading words that carry the claim. It errs toward merging near-duplicates at the
    same line, because two findings on one line are almost always one problem.
    """
    seen: set[tuple[str, int, str]] = set()
    seen_lines: dict[tuple[str, int], Finding] = {}
    out: list[Finding] = []
    for finding in findings:
        key = (finding.path, finding.line, _normalise(finding.title))
        if key in seen:
            continue
        # Same path and line is the same place; keep the first, which ranking has already put first.
        location = (finding.path, finding.line)
        if location in seen_lines:
            continue
        seen.add(key)
        seen_lines[location] = finding
        out.append(finding)
    return out


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())[:120]


def rank(findings: list[Finding]) -> list[Finding]:
    """Severity, then file, then line. Stable, so equal findings keep their discovery order."""
    return sorted(
        findings,
        key=lambda f: (
            SEVERITY_ORDER.get(f.severity, 9),
            f.path,
            f.line,
            f.title,
        ),
    )


def plan(findings: list[Finding]) -> PostingPlan:
    """Decide what is posted inline and what is only counted."""
    ordered = rank(dedupe(findings))
    important = [f for f in ordered if f.severity == "important"]
    nit = [f for f in ordered if f.severity == "nit"]
    other = [f for f in ordered if f.severity not in ("important", "nit")]

    posted_important = important[:MAX_IMPORTANT]
    posted_nit = nit[:MAX_NIT]
    to_post = rank(posted_important + posted_nit)

    omitted_important = max(0, len(important) - len(posted_important))
    omitted_nit = max(0, len(nit) - len(posted_nit))
    omitted = omitted_important + omitted_nit + len(other)

    return PostingPlan(
        to_post=to_post,
        omitted=omitted,
        omitted_important=omitted_important,
        omitted_nit=omitted_nit,
    )
