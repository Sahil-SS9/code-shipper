"""One place that decides which harness runs, and refuses before any work starts.

`HermesHarness()` was constructed at three call sites, so the README's "with any agent harness" was not
reachable from any command. Resolving here means the selection rules exist once: a harness `doctor` reports
UNSUPPORTED is refused with doctor's reason, an UNVERIFIED one runs only when the caller says so explicitly,
and every summary can name the harness, profile and model that actually ran.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapters import CommandHarness, HarnessError, HermesHarness, OpenAICompatHarness

# What the CLI accepts, in the order `--help` lists them.
HARNESS_NAMES = ("hermes", "claude", "codex", "opencode", "openai_compat")
DEFAULT_HARNESS = "hermes"


class HarnessRefused(Exception):
    """The selection cannot run here. Carries the exit code the CLI should use."""

    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class Selection:
    """What was chosen, and what the user must be told about it."""

    harness: Any
    name: str
    profile: str
    model: str
    unverified: bool
    reason: str

    def summary_fields(self) -> dict[str, str]:
        """The three facts every summary must name, so a review cannot misreport what ran."""
        return {"harness": self.name, "profile": self.profile, "model": self.model}

    def banner(self) -> str:
        flag = "  [UNVERIFIED]" if self.unverified else ""
        return f"harness: {self.name} · profile: {self.profile or '(default)'} · model: {self.model}{flag}"


def _model_for(harness: Any) -> str:
    for attribute in ("model", "_model", "model_name"):
        value = getattr(harness, attribute, None)
        if isinstance(value, str) and value:
            return value
    return "(harness default)"


def build_harness(
    *,
    name: str = DEFAULT_HARNESS,
    profile: str = "",
    allow_unverified: bool = False,
    config: Any = None,
) -> Selection:
    """Resolve the harness or refuse with a reason. Never returns a harness that cannot run.

    Refusing here rather than at first use is the point: an UNSUPPORTED harness must fail before a review
    starts, not after it has downloaded a diff and burned a budget.
    """
    if name not in HARNESS_NAMES:
        raise HarnessRefused(
            f"unknown harness {name!r}; choose one of: {', '.join(HARNESS_NAMES)}"
        )

    if name == "hermes":
        harness: Any = HermesHarness(profile=profile) if profile else HermesHarness()
    elif name == "openai_compat":
        try:
            harness = OpenAICompatHarness()
        except (HarnessError, TypeError, ValueError) as exc:
            raise HarnessRefused(f"openai_compat is UNSUPPORTED: {exc}") from exc
    else:
        try:
            harness = CommandHarness(preset=name)
        except HarnessError as exc:
            raise HarnessRefused(f"{name} is UNSUPPORTED: {exc}") from exc

    ok, reason = harness.available()
    if not ok:
        # doctor's own words, so the CLI, doctor and the refusal cannot disagree.
        raise HarnessRefused(f"{name} is UNSUPPORTED: {reason}")

    unverified = "UNVERIFIED" in reason.upper()
    if unverified and not allow_unverified:
        raise HarnessRefused(
            f"{name} is UNVERIFIED: {reason}. Re-run with --allow-unverified-harness to use it anyway; "
            "its output is not covered by any measured control."
        )

    # A profile only means something to a harness that has one. Silently ignoring it would be the same
    # class of bug as dropping HERMES_HOME: the user believes a setting applied when it did not.
    if profile and name != "hermes":
        raise HarnessRefused(
            f"--hermes-profile applies to the hermes harness, not {name}. "
            f"Drop it, or use --harness hermes."
        )

    return Selection(
        harness=harness,
        name=name,
        profile=profile if name == "hermes" else "",
        model=_model_for(harness),
        unverified=unverified,
        reason=reason,
    )
