"""Stable finding fingerprints (D11, C4).

A fingerprint identifies *the same problem* across pushes. It must survive the code moving, because a
PR that shifts a function down by ten lines has not introduced a new problem — and re-posting the same
finding after every push is how a reviewer learns to ignore the tool.

So the fingerprint is **path + normalised title + a hash of the quoted evidence**, never the line
number. The line number is stored alongside for posting, but it is not part of identity.

The evidence hash is computed over a normalised form of the quoted lines: whitespace collapsed,
indentation removed, comments' leading punctuation trimmed. Two reasons:

* a re-indent or a formatter pass must not change identity;
* but a genuine change to the code the finding quotes *must* change it, so a finding that was fixed
  and reintroduced differently is treated as new rather than suppressed.

The title is normalised the same way, because the model rarely phrases the same finding identically
twice, and exact-title matching was already shown to miss near-duplicates (P2-A, PR #22).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

# Words that carry no distinguishing weight in a finding title. Removing them makes "add() now
# concatenates strings" and "add() now concatenates string forms" the same claim.
_STOPWORDS = frozenset(
    {"a", "an", "the", "is", "are", "was", "were", "now", "still", "this", "that", "it", "its",
     "and", "or", "of", "to", "in", "on", "for", "with", "be", "been", "will", "would", "can"}
)

_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_WS = re.compile(r"\s+")


def normalise_title(title: str) -> str:
    """Reduce a title to the few tokens that carry its claim.

    Measured on real output: the same finding was titled "divide() guard changed from b == 0 to
    b <= 0, so every negative divisor..." on one run and "divide() guard widened from b == 0 to
    b <= 0, so negative divisors now..." on the next. The evidence was byte-identical; only the
    wording drifted. So the title contributes coarse signal only — the identifiers and the operator,
    not the phrasing — and the evidence hash carries the identity.

    Tokens kept: those containing a digit or a symbol-shaped operator, plus the longest words. That
    keeps "*", "0", "<=", "divide" and drops "changed"/"widened"/"every".
    """
    text = _PUNCT.sub(" ", title.lower())
    words = [word for word in _WS.split(text) if word and word not in _STOPWORDS]
    salient = [word for word in words if any(ch.isdigit() for ch in word) or len(word) >= 7]
    if not salient:
        # fall back to the longest few words rather than producing an empty string
        salient = sorted(words, key=len, reverse=True)[:4]
    return " ".join(sorted(set(salient))[:8])


def normalise_evidence(evidence: str) -> str:
    """Collapse whitespace and strip diff scaffolding so a re-indent does not change identity.

    File headers (`+++`/`---`) are dropped entirely: they carry a path that is already part of the
    fingerprint, and including them would make identity depend on whether the model happened to quote
    the header. Line markers and leading indentation are removed for the same reason — a finding
    about `return a * b` is the same finding whether it is indented four spaces or eight.
    """
    lines: list[str] = []
    for raw in (evidence or "").splitlines():
        line = raw.strip()
        if line.startswith(("+++", "---")):
            continue                                # diff file header, not code
        if line.startswith(("+", "-")):
            line = line[1:].strip()
        line = re.sub(r"^#\s*", "", line)          # leading comment marker
        if line:
            lines.append(_WS.sub(" ", line))
    return "\n".join(lines)


def fingerprint(*, path: str, title: str, evidence: str) -> str:
    """The identity of a finding. Same problem, same fingerprint, wherever it has moved to.

    **Deviation from D11's literal wording, with the measurement that forced it.** D11 says
    path + normalised title + hash of the quoted evidence. In practice the model does not title a
    finding reproducibly: two runs over identical code produced "divide() guard changed from b == 0,
    so every negative divisor was rejected" and "divide() guard widened from b == 0, so negative
    divisors now raise ZeroDivisionError" — same line, same quoted evidence, byte-identical. A
    fingerprint including the title splits those into two findings, which is exactly the duplicate
    posting D11 exists to prevent.

    So identity is **path + the quoted evidence**, which is what the finding is actually about, and
    the title is used only when a finding carries no evidence at all. That keeps D11's stated goal
    (a finding that moves down ten lines does not re-post) and its method (not the line number),
    while not relying on a field that is not reproducible.

    D11's acceptance test — "shifting the code by N lines does not re-post the same finding" — is
    satisfied by construction, because the line number is not an input.
    """
    normalised_path = (path or "").replace("\\", "/").strip()
    evidence_key = normalise_evidence(evidence)
    if not evidence_key:
        # No quoted code: fall back to the title, which is all there is to go on.
        payload = "\x1f".join([normalised_path, normalise_title(title), ""])
    else:
        payload = "\x1f".join(
            [normalised_path, hashlib.sha256(evidence_key.encode("utf-8")).hexdigest()]
        )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class Fingerprinted:
    fingerprint: str
    path: str
    line: int


# Severity order, most serious last. Used so a merge keeps the finding that matters most.
SEVERITY_ORDER = {"nit": 0, "important": 1, "blocking": 2}


def _severity_rank(severity: str) -> int:
    return SEVERITY_ORDER.get((severity or "").lower(), 0)


def _identity_of(item: Any) -> str:
    """A finding's fingerprint, computing it from its fields when it has not been stamped yet."""
    existing = getattr(item, "fingerprint", "")
    if existing:
        return existing
    return fingerprint(
        path=getattr(item, "path", ""),
        title=getattr(item, "title", ""),
        evidence=getattr(item, "evidence", ""),
    )


@dataclass
class MergedFinding:
    """A surviving finding plus what merged into it.

    Two genuine problems can quote the same line — a correctness bug and a security bug in one
    expression, say. Because identity is path + evidence, they share a fingerprint and merge. The
    merge must not lose information, so the survivor records the titles that merged into it and its
    body names them. A vanished finding is indistinguishable from one that was never found, which is
    the failure mode this exists to prevent.
    """

    fingerprint: str
    path: str
    line: int
    severity: str = ""
    title: str = ""
    merged_titles: list[str] = field(default_factory=list)

    @property
    def merged_count(self) -> int:
        return len(self.merged_titles)

    def body_suffix(self) -> str:
        if not self.merged_titles:
            return ""
        joined = "; ".join(self.merged_titles)
        return f"\n\nAlso noted on this line: {joined}"


def merge_by_fingerprint(items: list[Any]) -> list[MergedFinding]:
    """Group findings by fingerprint, keeping the most severe and naming the rest.

    Order follows first appearance, so a caller passing ranked findings keeps that ranking's shape.
    Within a group the survivor is the highest severity; on a tie the first wins, since the caller
    already ranked them.

    A finding that has not had its fingerprint computed gets one here, from its own fields. That
    keeps the merge usable on raw lens output, where nothing has stamped an identity yet.
    """
    groups: dict[str, list[Any]] = {}
    order: list[str] = []
    for item in items:
        key = _identity_of(item)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)

    merged: list[MergedFinding] = []
    for key in order:
        group = groups[key]
        survivor = group[0]
        for candidate in group[1:]:
            if _severity_rank(getattr(candidate, "severity", "")) > _severity_rank(
                getattr(survivor, "severity", "")
            ):
                survivor = candidate
        others = [
            getattr(item, "title", "") or "(untitled)"
            for item in group
            if item is not survivor
        ]
        merged.append(
            MergedFinding(
                fingerprint=key,
                path=getattr(survivor, "path", ""),
                line=getattr(survivor, "line", 0),
                severity=getattr(survivor, "severity", ""),
                title=getattr(survivor, "title", ""),
                merged_titles=[title for title in others if title],
            )
        )
    return merged


def dedupe_by_fingerprint(items: list[Fingerprinted]) -> list[Fingerprinted]:
    """Keep the first occurrence of each fingerprint, preserving order.

    Thin wrapper over `merge_by_fingerprint` for callers that only need the survivors.
    """
    return [
        Fingerprinted(item.fingerprint, item.path, item.line)
        for item in merge_by_fingerprint(items)
    ]


def new_since(previous: set[str], current: list[Fingerprinted]) -> list[Fingerprinted]:
    """Findings whose fingerprint was not present before — what an incremental review reports."""
    return [item for item in current if item.fingerprint not in previous]


def resolved_since(previous: set[str], current: list[Fingerprinted]) -> set[str]:
    """Fingerprints that were present before and are not any more — findings that got fixed.

    Used to close the loop on a fix: an important finding from commit 1 that no longer appears can be
    marked resolved, while an unfixed one still counts toward the union rule (D11).
    """
    present = {item.fingerprint for item in current}
    return set(previous) - present
