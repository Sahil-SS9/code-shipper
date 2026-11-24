"""The verification pass: try to refute each finding before it is posted.

A review that posts every guess is worse than no review, because a human then has to do the work
twice. FR-R2 requires verification at every level, including Quick: a cheap level loses lenses, never
the check that decides whether a finding is real.

Only `confirmed` findings are posted. `refuted` and `unsure` are recorded but never posted, which is
the deliberate direction to fail in.
"""

from __future__ import annotations

from dataclasses import dataclass

from .framing import untrusted_block
from .lenses import VERIFY_SCHEMA

BATCH_SIZE = 10


@dataclass(frozen=True)
class Finding:
    id: str
    path: str
    line: int
    severity: str
    title: str
    body: str
    evidence: str
    lens: str = ""
    end_line: int | None = None

    def as_dict(self) -> dict:
        data = {
            "id": self.id,
            "path": self.path,
            "line": self.line,
            "severity": self.severity,
            "title": self.title,
            "body": self.body,
            "evidence": self.evidence,
            "lens": self.lens,
        }
        if self.end_line:
            data["end_line"] = self.end_line
        return data


@dataclass(frozen=True)
class Verdict:
    id: str
    verdict: str
    reason: str = ""

    @property
    def confirmed(self) -> bool:
        return self.verdict == "confirmed"


def batch(findings: list[Finding], size: int = BATCH_SIZE) -> list[list[Finding]]:
    """Split findings into verification batches. One call per batch of <= 10 (FR-R2)."""
    if size <= 0:
        raise ValueError("batch size must be positive")
    return [findings[i : i + size] for i in range(0, len(findings), size)]


def build_prompt(findings: list[Finding], diff_text: str, *, intent: str = "") -> str:
    """Ask the verifier to refute, not to agree.

    The framing matters: a prompt that asks "is this finding correct?" invites agreement, while one
    that asks "show why this is wrong" surfaces the findings that do not survive contact with the
    code.

    ``intent`` is the PR author's own description, already framed as untrusted by the caller. It is
    useful here because a finding that contradicts the stated intent is worth re-reading, and a
    finding the description quietly admits is worth confirming.
    """
    lines = [
        "You are verifying code-review findings against the actual change. Your job is to REFUTE "
        "them. A finding survives only if the changed lines really do what it claims.",
        "",
        "For each finding, answer:",
        "- confirmed: the code genuinely has this problem, and the evidence quoted supports it",
        "- refuted: the claim is wrong, already handled, or refers to code the change did not touch",
        "- unsure: you cannot tell from the diff alone",
        "",
        "Be strict. A finding that a reasonable reviewer would not act on is refuted.",
        "",
        "Reply with JSON only: {\"verdicts\": [{\"id\": \"...\", \"verdict\": "
        "\"confirmed|refuted|unsure\", \"reason\": \"...\"}]}",
        "",
    ]
    if intent:
        lines += [intent, ""]
    lines.append(untrusted_block("diff", diff_text))
    lines.append("")
    # Finding text quotes the model's own earlier output, which quoted author-controlled code. It is
    # not trusted either, so it is framed the same way rather than concatenated raw.
    rendered: list[str] = []
    for finding in findings:
        rendered.append(
            f"- id={finding.id} path={finding.path} line={finding.line} "
            f"severity={finding.severity}\n  claim: {finding.title}\n  detail: {finding.body}\n"
            f"  evidence: {finding.evidence}"
        )
    lines.append(untrusted_block("findings", "\n".join(rendered)))
    return "\n".join(lines)


def apply_verdicts(findings: list[Finding], verdicts: list[Verdict]) -> list[Finding]:
    """Keep only confirmed findings, in their original order.

    A finding with no verdict is dropped: silence from the verifier is not confirmation.
    """
    confirmed = {v.id for v in verdicts if v.confirmed}
    return [f for f in findings if f.id in confirmed]


def parse_verdicts(payload: dict) -> list[Verdict]:
    """Read verdicts from harness output, ignoring entries that are malformed."""
    out: list[Verdict] = []
    for entry in payload.get("verdicts") or []:
        if not isinstance(entry, dict):
            continue
        ident = entry.get("id")
        verdict = entry.get("verdict")
        if not isinstance(ident, str) or verdict not in ("confirmed", "refuted", "unsure"):
            continue
        out.append(Verdict(id=ident, verdict=verdict, reason=str(entry.get("reason") or "")))
    return out


__all__ = [
    "Finding",
    "Verdict",
    "apply_verdicts",
    "batch",
    "build_prompt",
    "parse_verdicts",
    "VERIFY_SCHEMA",
]
