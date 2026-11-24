"""Review lenses: the kinds of problem each pass looks for.

A lens is a prompt and a schema, not a model. That keeps the engine testable without a model call and
makes it obvious that a lens cannot post anything: it returns findings, and the posting path is
somewhere else entirely.

Lens selection follows K7: Quick runs 1 combined lens, Standard 3, Deep 5. Every level runs the
verification pass, so a cheap level loses lenses, never verification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

LensName = Literal["correctness", "security", "edge", "tests", "contract", "combined"]

# The order matters: when a level selects a subset, it takes the first N in this order.
LENS_ORDER: tuple[str, ...] = ("correctness", "security", "edge", "tests", "contract")

LENS_COUNT = {"quick": 1, "standard": 3, "deep": 5}


@dataclass(frozen=True)
class Lens:
    name: str
    title: str
    instruction: str


LENSES: dict[str, Lens] = {
    "correctness": Lens(
        name="correctness",
        title="Correctness",
        instruction=(
            "Look for logic that does not do what the surrounding code and the change's own intent "
            "imply: wrong operators, inverted conditions, off-by-one, wrong variable, incorrect "
            "unpacking, mutated shared state, a return value that contradicts the name. Ignore "
            "style and naming here."
        ),
    ),
    "security": Lens(
        name="security",
        title="Security",
        instruction=(
            "Look for input that reaches a sink without validation: shell invocation, SQL or query "
            "construction, path traversal, deserialisation, secrets or tokens in code or logs, "
            "authorisation checks that can be skipped, and error paths that leak. Only report "
            "something the changed lines actually introduce."
        ),
    ),
    "edge": Lens(
        name="edge",
        title="Edge cases",
        instruction=(
            "Look for inputs and states the change does not handle: empty or single-element "
            "collections, None, zero, negative numbers, very large values, unicode, concurrent "
            "access, partial failure, retries, and time or timezone assumptions."
        ),
    ),
    "tests": Lens(
        name="tests",
        title="Tests",
        instruction=(
            "Look for behaviour that the change introduces or alters without a test that would fail "
            "if it were wrong, assertions that cannot fail, and tests that assert the "
            "implementation rather than the behaviour. Do not ask for coverage of unchanged code."
        ),
    ),
    "contract": Lens(
        name="contract",
        title="Contract and interfaces",
        instruction=(
            "Look for changes to a public interface, return shape, error type, file format or "
            "protocol that break an existing caller, and for callers updated in one place but not "
            "another. Compare against how the rest of the repository uses it."
        ),
    ),
    "combined": Lens(
        name="combined",
        title="Combined review",
        instruction=(
            "Review the change for correctness, security, edge cases, tests and interface breakage "
            "together. Prefer the most serious problems: a change that is wrong is more important "
            "than one that is merely untidy."
        ),
    ),
}


def select(level: str) -> list[Lens]:
    """The lenses for a review level. Quick is one combined lens, not a truncated deep one."""
    if level not in LENS_COUNT:
        raise ValueError(f"unknown review level: {level!r}")
    if level == "quick":
        return [LENSES["combined"]]
    return [LENSES[name] for name in LENS_ORDER[: LENS_COUNT[level]]]


FINDINGS_SCHEMA: dict = {
    "type": "object",
    "required": ["findings"],
    "additionalProperties": False,
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["path", "line", "severity", "title", "body", "evidence"],
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "severity": {"type": "string", "enum": ["important", "nit", "pre_existing"]},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "evidence": {"type": "string"},
                },
            },
        }
    },
}

VERIFY_SCHEMA: dict = {
    "type": "object",
    "required": ["verdicts"],
    "additionalProperties": False,
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "verdict"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["confirmed", "refuted", "unsure"]},
                    "reason": {"type": "string"},
                },
            },
        }
    },
}
