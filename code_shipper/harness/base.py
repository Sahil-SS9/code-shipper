"""The harness interface: how an agent is asked to do something.

The contract is deliberately small. A harness gets a task (prompt, workdir, output schema, budget)
and returns a result that either validates against that schema or is rejected. Nothing here decides
whether a change is allowed — that is the broker's and the scope check's job — and nothing here holds
a credential for the repository.

Two properties are enforced by tests rather than assumed (T-ADV-12): a result that does not match the
schema is rejected and produces no writes, and availability is reported honestly when a harness is
not installed instead of failing later mid-run.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

TaskKind = Literal["review_lens", "verify", "fix", "reply"]


class HarnessError(Exception):
    """The harness could not run, or produced something unusable."""


@dataclass(frozen=True)
class Budget:
    max_seconds: int = 300
    max_turns: int = 20
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        for name in ("max_seconds", "max_turns"):
            value = getattr(self, name)
            if not isinstance(value, int) or value <= 0:
                raise HarnessError(f"budget.{name} must be a positive integer")


@dataclass(frozen=True)
class AgentTask:
    kind: TaskKind
    prompt: str
    workdir: Path
    output_schema: dict[str, Any] = field(default_factory=dict)
    budget: Budget = field(default_factory=Budget)


@dataclass(frozen=True)
class Usage:
    cost_usd: float | None = None
    tokens: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"cost_usd": self.cost_usd, "tokens": self.tokens}


@dataclass(frozen=True)
class AgentResult:
    ok: bool
    output: dict[str, Any] | None = None
    raw_tail: str = ""
    usage: Usage | None = None
    error: str | None = None

    @property
    def rejected(self) -> bool:
        return not self.ok


class Harness(Protocol):
    name: str
    supported_kinds: frozenset[str]

    def available(self) -> tuple[bool, str]:
        """(installed_and_usable, human-readable reason). Honest: never guess yes."""
        ...

    def run(self, task: AgentTask, env: dict[str, str]) -> AgentResult: ...


# -- schema validation -----------------------------------------------------

_JSON_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


def validate_against_schema(value: Any, schema: dict[str, Any], *, path: str = "$") -> list[str]:
    """A small JSON-Schema subset: type, required, properties, items, enum.

    Deliberately not a full implementation. It covers the output schemas this project defines, and it
    fails closed: an unsupported keyword raises rather than being ignored, because silently skipping
    a constraint is how an invalid result gets posted.
    """
    if not isinstance(schema, dict):
        raise HarnessError(f"schema at {path} is not an object")

    supported = {"type", "required", "properties", "items", "enum", "description", "additionalProperties"}
    unknown = set(schema) - supported
    if unknown:
        raise HarnessError(f"unsupported schema keyword(s) at {path}: {sorted(unknown)}")

    errors: list[str] = []

    expected = schema.get("type")
    if expected is not None:
        kinds = expected if isinstance(expected, list) else [expected]
        for kind in kinds:
            if kind not in _JSON_TYPES:
                raise HarnessError(f"unsupported type {kind!r} at {path}")
        if not any(isinstance(value, _JSON_TYPES[k]) for k in kinds):
            errors.append(f"{path}: expected {expected}, got {type(value).__name__}")
            return errors

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required key {key!r}")
        properties = schema.get("properties", {})
        for key, sub in properties.items():
            if key in value:
                errors.extend(validate_against_schema(value[key], sub, path=f"{path}.{key}"))
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            if extra:
                errors.append(f"{path}: unexpected keys {sorted(extra)}")

    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            errors.extend(validate_against_schema(item, schema["items"], path=f"{path}[{index}]"))

    return errors


def parse_json_object(text: str) -> dict[str, Any]:
    """Extract the last JSON object from harness output.

    Harnesses wrap their answer in prose, and some emit a candidate object before the real one, so
    the *last* balanced object wins. Never invents a result: malformed output is an error.
    """
    if not isinstance(text, str):
        raise HarnessError("output is not text")
    candidates = _balanced_objects(text)
    for candidate in reversed(candidates):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise HarnessError("no JSON object in the output")


def _balanced_objects(text: str) -> list[str]:
    """Every top-level {...} substring, brace-balanced and quote-aware."""
    found: list[str] = []
    depth = 0
    start: int | None = None
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    found.append(text[start : index + 1])
                    start = None
    return found


def which(preset: str) -> str | None:
    """The executable for a command preset, or None when it is not installed."""
    return shutil.which(preset)


class FakeHarness:
    """A harness that returns a scripted result. No process, no network, no credential."""

    name = "fake"

    def __init__(self, result: AgentResult, *, available: tuple[bool, str] = (True, "fake")) -> None:
        self._result = result
        self._available = available
        self.tasks: list[AgentTask] = []

    def available(self) -> tuple[bool, str]:
        return self._available

    def run(self, task: AgentTask, env: dict[str, str]) -> AgentResult:
        self.tasks.append(task)
        return self._result

    @property
    def supported_kinds(self) -> frozenset[str]:
        return frozenset({"review_lens", "verify", "fix", "reply"})
