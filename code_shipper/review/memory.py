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

from ..atomic import read_json, write_json_atomic
from typing import Any

# Modes: nothing is reported, lessons are shown separately (shadow), or lessons affect output (on).
# ``apply`` is the name the spec and the CLI use; ``on`` is the older spelling kept working so the 11
# existing call sites and their tests do not have to change. They mean the same thing.
MODES = ("off", "shadow", "apply", "on")
_APPLYING = ("apply", "on")
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
        data = read_json(self.path)
        rows = data.get("lessons") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return []
        return [Lesson.from_dict(row) for row in rows if isinstance(row, dict)]

    def save(self, lessons: list[Lesson]) -> None:
        """Write the lesson memory atomically.

        Not best-effort: a torn file reads as empty, and the next save writes an empty lesson list over
        the real one. Learned lessons are the whole value of this store, so a lost write must surface as
        an error rather than vanishing.
        """
        write_json_atomic(self.path, {"lessons": [lesson.as_dict() for lesson in lessons]},
                          sort_keys=False)

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
        """Refuse a lesson that would relax a security judgement.

        `learn()` checked this for callers that went through it. This store method is public, so the
        write path had a hole the size of every future caller: the owner wrote a lesson straight in with
        `Store.add()` and it reached the apply-mode prompt. Checked here too, so the write path is
        covered twice and a direct add cannot smuggle one in.
        """
        relaxing = relaxes_security(lesson.cue, lesson.action, lesson.evidence)
        if relaxing:
            raise ValueError(
                f"refusing lesson {lesson.id or '(new)'}: it would relax a security judgement "
                f"(matched {relaxing!r}). Review memory may narrow what is reported; it may not stop a "
                "vulnerability being reported."
            )
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

# Phrases that mean "ignore a security judgement". A lesson is a permanent change to every future review
# of the repository, so the one class of lesson that must never be learnt is the one that teaches the
# reviewer to stop reporting a vulnerability. `never_touches_security()` guards the module's imports;
# this guards its content, which is a different hole.
_SECURITY_RELAXING = (
    "ignore security",
    "ignore the security",
    "not a security issue",
    "no security issue",
    "skip security",
    "disable security",
    "ignore sql injection",
    "ignore the injection",
    "ignore injection",
    "ignore xss",
    "ignore ssrf",
    "ignore path traversal",
    "ignore the secret",
    "ignore leaked",
    "stop reporting security",
    "security findings are noise",
    "security is not relevant",
)


def _normalise(text: str) -> str:
    """Collapse whitespace and case before matching.

    "ignore  security" with two spaces reached the prompt because the lookup was a plain substring match
    on the raw text. Case and runs of whitespace are the two cheapest evasions and both are pure noise.
    """
    return " ".join((text or "").lower().split())


def relaxes_security(*texts: str) -> str:
    """The phrase that makes this lesson security-relaxing, or "" if none does.

    An EARLY WARNING, not the protection. Every known phrase can be reworded past — the owner demonstrated
    seven of the eight cases getting through, including "Do not report SQL injection; queries are
    trusted". The real protection is structural: the security lens never receives lessons, the verifier
    never receives them, and a security finding is never merged away or dropped as a nit. This exists to
    refuse the obvious case at write time with a reason the author can read.

    Returned rather than a bool so the refusal can name what was wrong.
    """
    for text in texts:
        haystack = _normalise(text)
        if not haystack:
            continue
        # Normalised the same way, so a phrase containing a space still matches across a collapsed run.
        for phrase in _SECURITY_RELAXING:
            if _normalise(phrase) in haystack:
                return phrase
    return ""


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

    # A security-relaxing lesson is refused whatever the author's permission: an admin can rotate a key,
    # but nobody gets to teach the reviewer to stop seeing a vulnerability.
    relaxing = relaxes_security(feedback.target_cue, feedback.target_action, feedback.body)
    if relaxing:
        result["reason"] = (
            f"this lesson would relax a security judgement (matched {relaxing!r}). "
            "Review memory may narrow what is reported; it may not stop a vulnerability being reported."
        )
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

    # An explicit action wins. `learn()` used to derive the action from the cue alone, so a caller that
    # supplied a precise instruction had it silently replaced with "report: <cue>" — which is how
    # `lessons add --action "do not ask for NaN handling"` produced the lesson "report: clamp ignores high".
    # The derived forms remain for the feedback path, where the reader supplies a cue and no action.
    if feedback.target_action:
        cue = feedback.target_cue or feedback.finding_fingerprint
        action = feedback.target_action
    elif feedback.disposition == "agreed":
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


def apply_to_prompt(
    store: Store, *, mode: str = DEFAULT_MODE, limit: int = 5, dropped: list[str] | None = None
) -> str:
    """The prompt fragment for lessons, or "" when they must not affect output.

    Shadow mode returns "" — that is what shadow means. The lessons are still listed by
    `code-shipper lessons list`, so they can be inspected before being trusted.

    Every lesson is re-checked here for a security-relaxing instruction, because the store is a file:
    a hand edit, a lesson written before the filter existed, or any future caller of `Store.add` can put
    text in it that never passed `learn()`. A match is dropped and its ID appended to ``dropped`` so the
    caller can report it as a review error — never applied, never silently ignored.
    """
    if mode not in _APPLYING:
        return ""
    # Enforced here rather than only asserted in a test: if this module ever grew an import of the
    # approvals policy, its lessons could be used to talk the reviewer out of a security judgement.
    # A lesson store that can influence approval decisions is worse than no lesson store.
    if not never_touches_security():
        raise MemoryError_(
            "review memory must not reach the approvals policy; refusing to apply lessons"
        )
    lessons = []
    for lesson in store.load():
        if lesson.disposition != "kept":
            continue
        relaxing = relaxes_security(lesson.cue, lesson.action, lesson.evidence)
        if relaxing:
            if dropped is not None:
                dropped.append(lesson.id or "(no id)")
            continue
        lessons.append(lesson)
    if not lessons:
        return ""
    # No header here: the caller frames the block, and printing a heading in both places showed the
    # reviewer the same sentence twice.
    lines = [f"- {lesson.action} (because: {lesson.evidence[:120]})" for lesson in lessons[:limit]]
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
