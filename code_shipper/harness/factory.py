"""One place that decides which harness runs, and refuses before any work starts.

`HermesHarness()` was constructed at three call sites, so the README's "with any agent harness" was not
reachable from any command. Resolving here means the selection rules exist once: a harness `doctor` reports
UNSUPPORTED is refused with doctor's reason, an UNVERIFIED one runs only when the caller says so explicitly,
and every summary can name the harness, profile and model that actually ran.
"""

from __future__ import annotations

import subprocess
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
    provider: str = ""

    def summary_fields(self) -> dict[str, str]:
        """The three facts every summary must name, so a review cannot misreport what ran."""
        fields = {"harness": self.name, "profile": self.profile, "model": self.model}
        if self.provider:
            fields["provider"] = self.provider
        return fields

    def banner(self) -> str:
        flag = "  [UNVERIFIED]" if self.unverified else ""
        where = self.profile or "(default)"
        provider = f" · provider: {self.provider}" if self.provider else ""
        return f"harness: {self.name} · profile: {where} · model: {self.model}{provider}{flag}"


def _model_for(harness: Any, profile: str = "") -> str:
    """The model that will actually answer. Never a placeholder when it can be resolved.

    It used to return "(harness default)" whenever the harness object had no `model` attribute, which the
    Hermes harness does not — so every summary said the model was unknowable while the whole point of
    `--hermes-profile` is that the choice changes which model runs. `hermes config get model` is the
    supported way to ask, per profile, and it costs no tokens.
    """
    for attribute in ("model", "_model", "model_name"):
        value = getattr(harness, attribute, None)
        if isinstance(value, str) and value:
            return value

    resolved = _hermes_model(profile)
    if resolved:
        return resolved
    return unresolved_model_note(profile)


UNRESOLVED_MODEL = "(unknown"


def unresolved_model_note(profile: str = "") -> str:
    """Say *why* the model is unknown, in one line, instead of a placeholder that reads like an answer.

    The common cause, measured in the release-prep walkthrough: a throwaway HERMES_HOME plus
    `--hermes-profile` — Hermes looks the profile up under the effective home, where it does not exist.
    """
    import os

    home = os.environ.get("HERMES_HOME") or "~/.hermes"
    if profile:
        return f"(unknown: profile {profile} is not visible from HERMES_HOME {home})"
    return f"(unknown: `hermes config get model` gave no answer under HERMES_HOME {home})"


def _hermes_model(profile: str = "") -> str:
    """Ask Hermes which model is effective for a profile. Empty string when it cannot be determined.

    Reads `default:` from `hermes config get model`, which is YAML-ish and safe to parse line-wise. A
    failure here must not fail a review: an unknown model is reported as unknown, and the run continues.
    """
    argv = ["hermes"]
    if profile:
        argv += ["-p", profile]
    argv += ["config", "get", "model"]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    for line in proc.stdout.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "default" and value.strip():
            return value.strip()
    return ""


def _hermes_provider(profile: str = "") -> str:
    """The provider name, for the same reason. Empty when unknown."""
    argv = ["hermes"]
    if profile:
        argv += ["-p", profile]
    argv += ["config", "get", "model"]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    for line in proc.stdout.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "provider" and value.strip():
            return value.strip()
    return ""


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
        model=_model_for(harness, profile if name == "hermes" else ""),
        provider=_hermes_provider(profile) if name == "hermes" else "",
        unverified=unverified,
        reason=reason,
    )
