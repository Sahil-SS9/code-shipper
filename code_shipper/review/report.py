"""The posted review body, and its machine-readable verdict block.

C7/C8: the state is stated explicitly — complete, incomplete, or "no findings" — because a silent
review is indistinguishable from a broken one. An incomplete review says what failed; a review with
nothing to report says so in words.

C16: a fenced JSON block carries the same facts in a form a script can read, so nobody has to
regex-prose out of a comment.

The body never claims approval, a merge, or a request for changes. Those words do not appear even as
negations, because a reader skimming for "approved" should find nothing at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .fingerprint import Fingerprinted

MARKER = "<!-- code-shipper:review -->"


class ReviewState:
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


@dataclass
class ReviewReport:
    state: str
    level: str = "standard"
    profile: str = "standard"
    lenses_run: list[str] = field(default_factory=list)
    findings: list[Fingerprinted] = field(default_factory=list)
    important: int = 0
    nit: int = 0
    omitted: int = 0
    omitted_nits: int = 0
    omitted_pre_existing: int = 0
    dropped_unverified: int = 0
    merged_findings: int = 0
    incremental: bool = False
    since_head: str = ""
    errors: list[str] = field(default_factory=list)
    risk: str | None = None
    risk_reasons: list[str] = field(default_factory=list)

    # -- rendering ---------------------------------------------------------

    def render(self) -> str:
        lines = [MARKER, "## Code-Shipper review", ""]

        if self.incremental and self.since_head:
            lines += [f"_Incremental: commits since {self.since_head[:7]}._", ""]

        lines.append(self._state_line())
        lines.append("")

        if self.state == ReviewState.INCOMPLETE:
            for error in self.errors or ["no reason recorded"]:
                lines.append(f"- {error}")
            lines.append("")

        if self.omitted_pre_existing:
            lines.append(
                f"{self.omitted_pre_existing} pre-existing finding(s) omitted: this change did not "
                "introduce them."
            )
            lines.append("")
        if self.omitted_nits:
            lines.append(f"{self.omitted_nits} minor finding(s) not shown at this profile.")
            lines.append("")
        if self.dropped_unverified:
            lines.append(
                f"{self.dropped_unverified} candidate finding(s) were not confirmed on verification "
                "and are not listed."
            )
            lines.append("")
        if self.merged_findings:
            lines.append(
                f"{self.merged_findings} additional finding(s) shared a fingerprint with one above "
                "and are named in that finding's body rather than posted separately."
            )
            lines.append("")
        if self.risk:
            reasons = "; ".join(self.risk_reasons) if self.risk_reasons else "no reasons recorded"
            lines.append(f"Merge risk (advisory, not a gate): {self.risk} — {reasons}")
            lines.append("")

        lines += self.verdict_block().splitlines()
        lines += [
            "",
            "_Review only. Nothing here approves, requests changes, or merges. A human decides._",
        ]
        return "\n".join(lines)

    def _state_line(self) -> str:
        if self.state == ReviewState.INCOMPLETE:
            return "**INCOMPLETE** — this review did not finish, so the absence of findings is not a clean result."
        if not self.findings:
            return (
                f"No findings at {self.level} depth over "
                f"{', '.join(self.lenses_run) or 'no lenses'}. This is a complete review."
            )
        important = sum(1 for f in self.findings)
        return (
            f"{important} finding(s) at {self.level} depth "
            f"({', '.join(self.lenses_run) or 'no lenses'}), profile {self.profile}."
        )

    def verdict_block(self) -> str:
        payload: dict[str, Any] = {
            "state": self.state,
            "level": self.level,
            "profile": self.profile,
            "lenses": self.lenses_run,
            "incremental": self.incremental,
            "since_head": self.since_head or None,
            "counts": {
                "posted": len(self.findings),
                "omitted": self.omitted,
                "omitted_nits": self.omitted_nits,
                "omitted_pre_existing": self.omitted_pre_existing,
                "dropped_unverified": self.dropped_unverified,
                "merged_away": self.merged_findings,
            },
            "findings": [
                {"fingerprint": f.fingerprint, "path": f.path, "line": f.line}
                for f in self.findings
            ],
        }
        # Advisory only, and named so: the approvals evaluator must never read this (D15a).
        if self.risk:
            payload["merge_risk_advisory"] = self.risk
        return "```json\n" + json.dumps(payload, indent=2) + "\n```"
