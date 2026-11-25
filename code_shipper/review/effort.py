"""Low-effort / AI-generated PR detection (D15b, Phase 4b).

This is the component most likely to become a way to dismiss contributors, so its limits are structural
rather than advisory:

**Off by default.** Nothing happens unless a repo turns it on.

**When on, at most one label and one explanatory note.** There is no close, block, or request-changes
path in this module at all — not disabled, absent. A detector that can close a PR is a moderation tool,
and this is not one.

**It never claims authorship it cannot know.** "Looks machine-generated" is a guess from surface
signals; the note says so, names the signals it used, and says the author may disagree. Authorship is
not detectable from a diff, and pretending otherwise is how a useful signal becomes an insult.

The signals are also deliberately weak individually: a very uniform diff, a large change with no tests,
commit-message boilerplate. None is evidence on its own, which is why the threshold requires several.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# The only output this module may produce. Named so a caller cannot invent a third.
LABEL = "needs-author-context"

# Phrases that recur in generated commit messages. Each is weak; the score is what matters.
_BOILERPLATE = (
    "comprehensive",
    "robust",
    "seamless",
    "leverage",
    "enhance the",
    "ensure that",
    "this commit",
    "this change",
    "best practices",
    "industry-standard",
)


@dataclass
class Signal:
    name: str
    detail: str
    weight: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "detail": self.detail, "weight": self.weight}


@dataclass
class Detection:
    enabled: bool = False
    triggered: bool = False
    signals: list[Signal] = field(default_factory=list)
    label: str = ""
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "triggered": self.triggered,
            "signals": [signal.as_dict() for signal in self.signals],
            # Actions are explicit and exhaustive: a caller cannot read this and conclude it may close.
            "actions": {"label": bool(self.triggered and self.label),
                        "note": bool(self.triggered and self.note),
                        "close": False, "block": False, "request_changes": False},
            "label": self.label,
            "note": self.note,
        }


def _diff_uniformity(patch: str) -> float:
    """How repetitive a patch is: the share of added lines that duplicate an earlier one.

    A generated change often adds many near-identical blocks. This is a weak signal on its own — a
    mechanical rename looks the same — so it only counts alongside others.
    """
    added = [line[1:].strip() for line in patch.splitlines()
             if line.startswith("+") and not line.startswith("+++")]
    added = [line for line in added if line]
    if len(added) < 20:
        return 0.0
    seen: set[str] = set()
    duplicates = 0
    for line in added:
        if line in seen:
            duplicates += 1
        seen.add(line)
    return duplicates / len(added)


def _boilerplate_count(message: str) -> int:
    lowered = (message or "").lower()
    return sum(1 for phrase in _BOILERPLATE if phrase in lowered)


def detect(
    *,
    enabled: bool = False,
    patch: str = "",
    commit_message: str = "",
    lines_changed: int = 0,
    touches_tests: bool | None = None,
    threshold: int = 3,
) -> Detection:
    """Decide whether to add one label and one note. Never anything else."""
    if not enabled:
        # Off means off: no signals are even computed, so nothing can leak into a log as a judgement.
        return Detection(enabled=False, triggered=False)

    signals: list[Signal] = []

    uniformity = _diff_uniformity(patch)
    if uniformity >= 0.5:
        signals.append(Signal(
            "repetitive-diff",
            f"{int(uniformity * 100)}% of added lines repeat an earlier line",
            2,
        ))

    if lines_changed > 400 and touches_tests is False:
        signals.append(Signal(
            "large-without-tests",
            f"{lines_changed} lines changed with no test changes",
        ))

    boilerplate = _boilerplate_count(commit_message)
    if boilerplate >= 2:
        signals.append(Signal(
            "commit-message-boilerplate",
            f"{boilerplate} generic phrases in the commit message",
            boilerplate,
        ))

    # A TODO or placeholder left in the diff is the strongest single signal: it suggests code that was
    # written to look complete rather than to work.
    if re.search(r"^\+.*\b(TODO|FIXME|placeholder|implement this|your code here)\b",
                 patch, re.MULTILINE | re.IGNORECASE):
        signals.append(Signal(
            "placeholder-code", "the diff adds a placeholder or TODO where code should be", 2
        ))

    weight = sum(signal.weight for signal in signals)
    triggered = weight >= threshold

    detection = Detection(enabled=True, triggered=triggered, signals=signals)
    if triggered:
        detection.label = LABEL
        detection.note = _note(signals)
    return detection


def _note(signals: list[Signal]) -> str:
    """The one explanatory note. It names its signals and disclaims what it cannot know."""
    listed = "\n".join(f"- {signal.detail}" for signal in signals)
    return (
        "**This pull request may need more context from its author.**\n\n"
        "Some surface signals suggested the change was produced quickly:\n\n"
        f"{listed}\n\n"
        "These are heuristics, not proof, and they say nothing about authorship — that is not "
        "something a diff can show. If the change is deliberate, say so and this note can be ignored; "
        "nothing here blocks, closes or changes the status of this pull request."
    )
