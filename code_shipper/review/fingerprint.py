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
    # Carried so `enclosing_function` can read the quoted code. Without it the root-cause merge cannot
    # tell which function a finding is in, and silently merges nothing — which is how this was found.
    evidence: str = ""
    # Which lens produced it. Carried so the merge can refuse to absorb or be absorbed by a security
    # finding: without it a security finding could vanish into a lower-severity neighbour and nothing
    # downstream could tell that one had ever existed.
    lens: str = ""

    @property
    def merged_count(self) -> int:
        return len(self.merged_titles)

    def body_suffix(self) -> str:
        if not self.merged_titles:
            return ""
        joined = "; ".join(self.merged_titles)
        return f"\n\nAlso noted: {joined}"


_DEF = re.compile(r"^\s*(?:async\s+)?(?:def|function|func|fn)\s+([A-Za-z_][\w]*)", re.MULTILINE)


def enclosing_function(evidence: str) -> str:
    """The function name a quoted snippet sits in, or '' if it cannot be told.

    Read from the code the finding quotes rather than from a line number: the whole point of this module
    is that line numbers move. A `def` in the quoted text is a strong signal; the top-level case (no
    enclosing def in the snippet) reports '' and is deliberately *not* merged by this rule, because
    "somewhere in this file" is not a root cause.
    """
    matches = _DEF.findall(evidence or "")
    if not matches:
        return ""
    # The last def in a quoted window is the innermost one, which is the function the snippet is about.
    return matches[-1]


# The lens name that must never be merged away or downgraded. Imported lazily by name to avoid a cycle:
# engine imports this module, so this module cannot import engine at module level.
SECURITY_LENS = "security"


def _root_cause_key(item: Any) -> str | None:
    """A key that groups two findings about the same root cause, or None if it is not safe to guess.

    The case this exists for: one review reported the same missing upper bound at `scn05_clamp.py:2` and
    `:4`. Identity-by-evidence cannot merge them — the quoted lines differ — so the PR counted three
    important findings when there were two, and a maintainer reading "3" would look for a third bug.

    The rule is deliberately narrow: same path, same enclosing function, and a normalised title that
    agrees. Two findings in one function with different claims stay separate; two findings in one file
    with no enclosing function at all stay separate. Under-merging leaves a duplicate; over-merging hides
    a bug, and hiding a bug is the worse of the two.
    """
    path = str(getattr(item, "path", "") or "")
    function = enclosing_function(str(getattr(item, "evidence", "") or ""))
    title = normalise_title(str(getattr(item, "title", "") or ""))
    if not path or not function or not title:
        return None
    return f"{path}::{function}::{title}"


def _merged_label(survivor: MergedFinding, other: MergedFinding) -> str:
    """How a merged-away finding is named under the survivor.

    Two rules, both learned from the #25 output:

    * **Never restate the survivor's own title.** The first version appended `other.title` whenever it
      differed textually, so a finding merged with an identical title produced "Also noted: <the same
      title you are reading now>", which tells a reader nothing and reads like a bug.
    * **Never cut a title mid-word.** A surface clipping the joined string produced "PR adds an
      undocumented w", which is not a title. Truncation happens at word boundaries, and only after the
      line has been appended, so the reader always gets whole words.

    When the titles agree, the only thing worth saying is *where else*: the line number, if it differs.
    """
    same_claim = normalise_title(other.title) == normalise_title(survivor.title)
    if same_claim:
        if other.line and other.line != survivor.line:
            return f"the same finding at line {other.line}"
        return "the same finding quoted twice"
    label = (other.title or "").strip() or "an untitled finding"
    if other.line and other.line != survivor.line:
        return f"{label} (line {other.line})"
    return label


def merge_by_root_cause(items: list[Any]) -> list[MergedFinding]:
    """Merge findings about the same root cause, in addition to the fingerprint merge.

    Applied after `merge_by_fingerprint`, so identical evidence is already collapsed and this only has to
    catch the same defect quoted at two different lines.
    """
    merged = merge_by_fingerprint(items)
    groups: dict[str, list[MergedFinding]] = {}
    order: list[str] = []
    for finding in merged:
        key = _root_cause_key(finding)
        if key is None:
            # No safe key: keep it as its own group, addressed by fingerprint so it cannot collide.
            key = f"fp::{finding.fingerprint}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(finding)

    out: list[MergedFinding] = []
    for key in order:
        group = groups[key]
        if len(group) == 1:
            out.append(group[0])
            continue
        # A security finding is never merged away and never absorbs another. Two findings about one
        # expression can be a correctness bug and a vulnerability; collapsing them leaves one line in the
        # posted review where a maintainer needed two, which is the "missed bug" this merge must not
        # cause. Security findings therefore come out as their own entries, always.
        security = [item for item in group if item.lens == SECURITY_LENS]
        rest = [item for item in group if item.lens != SECURITY_LENS]
        out.extend(security)
        if len(rest) <= 1:
            out.extend(rest)
            continue
        survivor = max(rest, key=lambda f: _severity_rank(f.severity))
        for other in rest:
            if other is survivor:
                continue
            survivor.merged_titles.append(_merged_label(survivor, other))
        out.append(survivor)
    return out


def merge_by_fingerprint(items: list[Any]) -> list[MergedFinding]:
    """Group findings by fingerprint, keeping the most severe and naming the rest.

    Order follows first appearance, so a caller passing ranked findings keeps that ranking's shape.
    Within a group the survivor is the highest severity; on a tie the first wins, since the caller
    already ranked them.

    A finding that has not had its fingerprint computed gets one here, from its own fields. That
    keeps the merge usable on raw lens output, where nothing has stamped an identity yet.
    """
    # Grouped by identity AND lens. A security finding and a correctness finding can quote the same line —
    # one expression, two defects — and they share a fingerprint. Grouping on the fingerprint alone
    # collapsed them into one entry, which is exactly the merged-away vulnerability this must not do.
    groups: dict[tuple[str, str], list[Any]] = {}
    order: list[tuple[str, str]] = []
    for item in items:
        key = (_identity_of(item), getattr(item, "lens", "") or "")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)

    merged: list[MergedFinding] = []
    for key in order:
        group = groups[key]
        survivor = group[0]
        lens = getattr(survivor, "lens", "") or ""
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
                fingerprint=key[0],
                path=getattr(survivor, "path", ""),
                line=getattr(survivor, "line", 0),
                severity=getattr(survivor, "severity", ""),
                title=getattr(survivor, "title", ""),
                merged_titles=[title for title in others if title],
                evidence=getattr(survivor, "evidence", "") or getattr(survivor, "body", ""),
                lens=lens,
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


def apply_root_cause_merge(findings: list[Any]) -> list[Any]:
    """Merge by root cause, returning findings of the caller's own type.

    `merge_by_root_cause` works in `MergedFinding`; this keeps the rest of the pipeline in `Finding` so
    nothing downstream has to know a merge happened. The survivor keeps its identity and gains the
    merged titles in `body`, which is what `body_suffix` is for — the posted comment must name them.

    Returns the original list untouched when nothing merged, so a caller can tell the difference.
    """
    if len(findings) < 2:
        return findings
    merged = merge_by_root_cause(findings)
    if len(merged) == len(findings):
        return findings

    # Keyed by fingerprint AND lens: a security finding and a correctness finding can share a
    # fingerprint, and they now come out as two entries. Keying on the fingerprint alone would let the
    # second overwrite the first in this dict and drop it from `out` — a lost finding in the one place
    # that exists to prevent lost findings.
    by_key = {(_identity_of(item), getattr(item, "lens", "") or ""): item for item in findings}
    out: list[Any] = []
    for survivor in merged:
        original = by_key.get((survivor.fingerprint, survivor.lens))
        if original is None:
            # Fall back to the fingerprint alone, then keep the entry. Should not happen: the survivor
            # came from this list. Keeping it rather than dropping it is the safe direction — a lost
            # finding is indistinguishable from one never found.
            original = next(
                (item for item in findings
                 if _identity_of(item) == survivor.fingerprint
                 and (getattr(item, "lens", "") or "") == survivor.lens),
                None,
            )
        if original is None:
            continue
        suffix = survivor.body_suffix()
        if suffix and hasattr(original, "body"):
            # `Finding` is frozen, deliberately — identity must not drift once stamped. A merged body is
            # a new value, so it is a new finding.
            import dataclasses

            original = dataclasses.replace(
                original, body=f"{getattr(original, 'body', '') or ''}{suffix}"
            )
        out.append(original)
    return out
