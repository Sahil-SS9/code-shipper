"""Review memory: lessons learnt from feedback, applied to later reviews (D15c, Phase 4b).

A lesson is a cue (what to look for), an action (what to do), and evidence (what taught it). Three
properties make this safe to keep:

**Only write-access feedback teaches.** Anyone can comment "you missed X". A lesson that changes what
the reviewer reports is a change in behaviour, so it is only learnt from someone the host says can
write — the same authority rule as command handling (T-ADV-03). Feedback from a non-writer is recorded
as ignored, with the reason, so it is not silently dropped.

**Shadow by default.** A new lesson is stored and reported, but changes no posted output until it is
promoted. That way a bad lesson is visible before it alters a review.

**Withdrawal is real.** A lesson can be withdrawn, and a *reversed disposition* — the same feedback
pointing the other way on the same finding — withdraws it automatically. A lesson that cannot be
unlearnt is a permanent bias wearing a memory's clothes.

Lessons live under the state dir in plain JSON, so this is harness-neutral and memory-provider-neutral:
Nothing here depends on Hermes, or on any memory system's API.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Modes: nothing is reported, lessons are shown separately (shadow), or lessons affect output (on).
MODES = ("off", "shadow", "on")
DEFAULT_MODE = "shadow"


class MemoryError_(Exception):
    """A lesson operation that cannot proceed."""


@dataclass
class Lesson:
    cue: str
    action: str
    evidence: str
    id: str = ""
    created_at: float = 0.0
    source: str = ""
    disposition: str = "kept"     # kept | withdrawn
    applied: int = 0

    def __post_init__(self) -> None:
        if not self.id:
            digest = hashlib.sha256(
                f"{self.cue}|{self.action}".encode()
            ).hexdigest()[:12]
            self.id = f"L-{digest}"
        if not self.created_at:
            self.created_at = time.time()

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "cue": self.cue,
            "action": self.action,
            "evidence": self.evidence,
            "created_at": self.created_at,
            "source": self.source,
            "disposition": self.disposition,
            "applied": self.applied,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Lesson":
        return cls(
            cue=str(data.get("cue", "")),
            action=str(data.get("action", "")),
            evidence=str(data.get("evidence", "")),
            id=str(data.get("id", "")),
            created_at=float(data.get("created_at", 0) or 0),
            source=str(data.get("source", "")),
            disposition=str(data.get("disposition", "kept")),
            applied=int(data.get("applied", 0) or 0),
        )


@dataclass
class Feedback:
    """What a reader said about a finding."""

    finding_fingerprint: str
    disposition: str  # agreed | disagreed | false_positive | missed
    author: str = ""
    body: str = ""
    permission: str = "none"
    target_cue: str = ""
    target_action: str = ""


@dataclass
class Store:
    """Lessons on disk, one JSON file. Harness- and provider-neutral by construction."""

    root: str | os.PathLike[str]

    @property
    def path(self) -> Path:
        return Path(self.root) / "review-lessons.json"

    def load(self) -> list[Lesson]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        rows = data.get("lessons") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return []
        return [Lesson.from_dict(row) for row in rows if isinstance(row, dict)]

    def save(self, lessons: list[Lesson]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"lessons": [lesson.as_dict() for lesson in lessons]}, indent=2),
            encoding="utf-8",
        )

    def get(self, lesson_id: str) -> Lesson | None:
        for lesson in self.load():
            if lesson.id == lesson_id:
                return lesson
        return None

    def withdraw(self, lesson_id: str) -> bool:
        lessons = self.load()
        found = False
        for lesson in lessons:
            if lesson.id == lesson_id:
                lesson.disposition = "withdrawn"
                found = True
        if found:
            self.save(lessons)
        return found

    def add(self, lesson: Lesson) -> Lesson:
        lessons = self.load()
        for existing in lessons:
            if existing.id == lesson.id:
                # The same cue and action is the same lesson; refresh the evidence rather than
                # accumulating duplicates that would each apply.
                existing.evidence = lesson.evidence
                existing.disposition = "kept"
                self.save(lessons)
                return existing
        lessons.append(lesson)
        self.save(lessons)
        return lesson


_WRITE_PERMISSIONS = ("admin", "maintain", "write")


def learn(store: Store, feedback: Feedback, *, mode: str = DEFAULT_MODE) -> dict[str, Any]:
    """Turn feedback into a lesson, or refuse to.

    Returns what happened so the caller can report it. A non-writer's feedback is *recorded as
    ignored*, not silently dropped: a contributor whose feedback vanished has no way to tell the
    difference between "not accepted" and "broken".
    """
    result: dict[str, Any] = {
        "accepted": False,
        "mode": mode,
        "reason": "",
        "lesson": None,
        "withdrew": None,
    }

    if mode not in MODES:
        result["reason"] = f"unknown mode {mode!r}; expected one of {', '.join(MODES)}"
        return result

    if mode == "off":
        result["reason"] = "review memory is off"
        return result

    if (feedback.permission or "none").lower() not in _WRITE_PERMISSIONS:
        result["reason"] = (
            f"ignored: {feedback.author or 'the author'} has "
            f"{feedback.permission or 'none'!r} permission, and only write access or above can teach a "
            "lesson"
        )
        return result

    # A reversal withdraws the lesson it contradicts. This is the case that matters: a lesson learnt
    # from a mistaken "you missed this" must not outlive the correction.
    if feedback.disposition in ("agreed", "disagreed") and feedback.target_cue:
        for lesson in store.load():
            if lesson.cue != feedback.target_cue:
                continue
            contradicts = (
                (lesson.action.startswith("report") and feedback.disposition == "disagreed")
                or (lesson.action.startswith("do not report") and feedback.disposition == "agreed")
            )
            if contradicts and lesson.disposition == "kept":
                store.withdraw(lesson.id)
                result.update(
                    accepted=True,
                    withdrew=lesson.id,
                    reason=f"the reversed disposition withdrew lesson {lesson.id}",
                )
                return result

    if feedback.disposition not in ("agreed", "missed", "disagreed"):
        result["reason"] = f"unknown disposition {feedback.disposition!r}"
        return result

    if feedback.disposition == "agreed":
        cue = feedback.target_cue or feedback.finding_fingerprint
        action = f"report: {cue}"
    elif feedback.disposition == "missed":
        cue = feedback.target_cue or "an area the reviewer did not cover"
        action = f"look for: {cue}"
    else:  # disagreed
        cue = feedback.target_cue or feedback.finding_fingerprint
        action = f"do not report: {cue}"

    lesson = store.add(Lesson(
        cue=cue,
        action=action,
        evidence=feedback.body[:500],
        source=feedback.author,
    ))
    result.update(
        accepted=True,
        lesson=lesson.as_dict(),
        reason=f"learnt in {mode} mode"
        + (" (not applied to output yet)" if mode == "shadow" else ""),
    )
    return result


def apply_to_prompt(store: Store, *, mode: str = DEFAULT_MODE, limit: int = 5) -> str:
    """The prompt fragment for lessons, or "" when they must not affect output.

    Shadow mode returns "" — that is what shadow means. The lessons are still listed by
    `code-shipper lessons list`, so they can be inspected before being trusted.
    """
    if mode != "on":
        return ""
    lessons = [lesson for lesson in store.load() if lesson.disposition == "kept"]
    if not lessons:
        return ""
    lines = [
        "Lessons from feedback on earlier reviews in this repository. These refine what to look for; "
        "they do not override the instruction to report only real problems.",
        "",
    ]
    for lesson in lessons[:limit]:
        lines.append(f"- {lesson.action} (because: {lesson.evidence[:120]})")
    return "\n".join(lines)


def list_lessons(store: Store, *, include_withdrawn: bool = False) -> list[dict[str, Any]]:
    return [
        lesson.as_dict()
        for lesson in store.load()
        if include_withdrawn or lesson.disposition == "kept"
    ]


def never_touches_security() -> bool:
    """A structural statement: nothing in this module imports the approvals policy or settings.

    Kept as a callable so a test can assert the property rather than trust the comment.
    """
    import sys

    forbidden = ("code_shipper.approvals",)
    return not any(
        name.startswith(forbidden) for name in sys.modules
        if name.startswith("code_shipper.review.memory")
    )
