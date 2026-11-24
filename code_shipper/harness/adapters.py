"""Concrete harness adapters: hermes, command presets, and an OpenAI-compatible direct API.

Shared rules for all of them:

* the prompt goes in on **stdin**, never through a shell, so untrusted text is not interpreted
* the returned object is validated against the task's schema; a mismatch means no result
* availability is reported honestly: a preset whose binary is missing says so
* no adapter reads a credential from the environment for the repository; the process environment it
  receives is supplied by the caller and is already scrubbed
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .base import (
    AgentResult,
    AgentTask,
    HarnessError,
    Usage,
    parse_json_object,
    validate_against_schema,
    which,
)

CLEAN_ENV_KEYS = frozenset({"PATH", "LANG", "LC_ALL", "TERM", "TMPDIR", "HOME", "USER", "LOGNAME"})


def scrubbed_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Keep only the variables a subprocess needs.

    This is hygiene, not the control: the containment in the container spec is what carries the
    boundary. But an adapter run outside a container should still not inherit tokens.
    """
    source = os.environ if base is None else base
    out: dict[str, str] = {}
    for key, value in source.items():
        if key in CLEAN_ENV_KEYS:
            out[key] = value
            continue
        upper = key.upper()
        if upper.startswith(("GH_", "GITHUB_")):
            continue
        if upper.endswith(("_TOKEN", "_KEY", "_SECRET", "_PASSWORD")):
            continue
        if "SECRET" in upper or "PASSWORD" in upper:
            continue
    return out


def _validate(result: dict[str, Any], task: AgentTask) -> dict[str, Any]:
    if not task.output_schema:
        return result
    errors = validate_against_schema(result, task.output_schema)
    if errors:
        raise HarnessError("result does not match the schema: " + "; ".join(errors))
    return result


class HermesHarness:
    """Runs `hermes chat` one-shot with the prompt on stdin.

    Flags verified against the upstream CLI reference at the pinned commit. The model call leaves
    through whatever the caller has configured; inside a container that is the host inference broker,
    so this adapter never needs a provider credential itself.
    """

    name = "hermes"
    supported_kinds = frozenset({"review_lens", "verify", "fix", "reply"})

    def __init__(self, *, profile: str | None = None, hermes_bin: str = "hermes") -> None:
        self._profile = profile
        self._bin = hermes_bin

    def available(self) -> tuple[bool, str]:
        found = which(self._bin)
        if not found:
            return False, f"{self._bin} not found on PATH"
        try:
            proc = subprocess.run(
                [found, "--version"], capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"{self._bin} could not be run: {exc}"
        if proc.returncode != 0:
            return False, f"{self._bin} --version exited {proc.returncode}"
        version = (proc.stdout or proc.stderr).strip().splitlines()[0] if (proc.stdout or proc.stderr) else ""
        return True, version or "hermes present"

    def argv(self, task: AgentTask) -> list[str]:
        argv = [self._bin, "chat", "--query-file", "-", "--oneshot", "-Q",
                "--source", "tool",
                "--max-turns", str(task.budget.max_turns),
                "--run-budget", str(task.budget.max_seconds)]
        if task.kind in ("review_lens", "verify"):
            # no tools for reading tasks: they must not edit anything
            argv += ["-t", ""]
        if self._profile:
            argv += ["-p", self._profile]
        return argv

    def run(self, task: AgentTask, env: dict[str, str]) -> AgentResult:
        argv = self.argv(task)
        try:
            proc = subprocess.run(
                argv,
                input=task.prompt,
                cwd=str(task.workdir),
                env=env,
                capture_output=True,
                text=True,
                timeout=task.budget.max_seconds + 30,
            )
        except subprocess.TimeoutExpired:
            return AgentResult(ok=False, error="harness timed out")
        except OSError as exc:
            return AgentResult(ok=False, error=f"harness could not start: {exc}")

        tail = (proc.stdout or "")[-4000:]
        if proc.returncode != 0:
            return AgentResult(ok=False, raw_tail=tail, error=f"hermes exited {proc.returncode}")
        try:
            output = _validate(parse_json_object(proc.stdout or ""), task)
        except HarnessError as exc:
            return AgentResult(ok=False, raw_tail=tail, error=str(exc))
        return AgentResult(ok=True, output=output, raw_tail=tail, usage=Usage())


class CommandHarness:
    """Runs a configured argv with the prompt on stdin.

    Presets exist for the common CLIs, and each carries a `verified` flag that starts false. A preset
    is only trusted once its contract test has passed on a real install (T-CONTRACT-01).
    """

    name = "command"

    PRESETS: dict[str, dict[str, Any]] = {
        "claude": {"argv": ["claude", "-p"], "verified": False},
        "codex": {"argv": ["codex", "exec"], "verified": False},
        "opencode": {"argv": ["opencode", "run"], "verified": False},
    }

    def __init__(self, argv: list[str] | None = None, *, preset: str | None = None) -> None:
        if preset is not None:
            if preset not in self.PRESETS:
                raise HarnessError(f"unknown preset: {preset!r}")
            self._argv = list(self.PRESETS[preset]["argv"])
            self._preset = preset
        elif argv:
            self._argv = list(argv)
            self._preset = None
        else:
            raise HarnessError("a command harness needs an argv or a preset")
        if not self._argv:
            raise HarnessError("argv must not be empty")

    @property
    def supported_kinds(self) -> frozenset[str]:
        return frozenset({"review_lens", "verify", "fix", "reply"})

    @property
    def preset_verified(self) -> bool:
        return bool(self.PRESETS.get(self._preset or "", {}).get("verified", False))

    def available(self) -> tuple[bool, str]:
        found = which(self._argv[0])
        if not found:
            label = f"preset {self._preset}" if self._preset else "command"
            return False, f"{label}: {self._argv[0]} not found on PATH"
        if self._preset and not self.preset_verified:
            return True, f"{self._preset} present but preset is UNVERIFIED until T-CONTRACT-01 passes"
        return True, f"{self._argv[0]} present"

    def run(self, task: AgentTask, env: dict[str, str]) -> AgentResult:
        try:
            proc = subprocess.run(
                self._argv,
                input=task.prompt,
                cwd=str(task.workdir),
                env=env,
                capture_output=True,
                text=True,
                timeout=task.budget.max_seconds + 30,
            )
        except subprocess.TimeoutExpired:
            return AgentResult(ok=False, error="command timed out")
        except OSError as exc:
            return AgentResult(ok=False, error=f"command could not start: {exc}")

        tail = (proc.stdout or "")[-4000:]
        if proc.returncode != 0:
            return AgentResult(ok=False, raw_tail=tail, error=f"{self._argv[0]} exited {proc.returncode}")
        try:
            output = _validate(parse_json_object(proc.stdout or ""), task)
        except HarnessError as exc:
            return AgentResult(ok=False, raw_tail=tail, error=str(exc))
        return AgentResult(ok=True, output=output, raw_tail=tail, usage=Usage())


class OpenAICompatHarness:
    """Direct API adapter. Review and verify only (K8, D3).

    Fix runs need a contained provider-neutral tool loop, which does not exist yet. Saying so here
    means the caller is refused at construction rather than discovering it when a fix silently does
    nothing.
    """

    name = "openai_compat"
    supported_kinds = frozenset({"review_lens", "verify"})

    def __init__(self, base_url: str, api_key: str, model: str, *, timeout: float = 120.0) -> None:
        if not base_url or not model:
            raise HarnessError("base_url and model are required")
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._model = model
        self._timeout = timeout

    def __repr__(self) -> str:
        return f"OpenAICompatHarness(base_url={self._base!r}, model={self._model!r}, key=<redacted>)"

    def available(self) -> tuple[bool, str]:
        if not self._key:
            return False, "no API key configured"
        return True, f"{self._model} at {self._base}"

    def run(self, task: AgentTask, env: dict[str, str]) -> AgentResult:
        if task.kind not in self.supported_kinds:
            return AgentResult(
                ok=False,
                error=f"{task.kind} is not supported by the direct-API adapter: it is review-only in "
                "v1, and a fix needs a contained tool loop that is not built",
            )
        import urllib.error
        import urllib.request

        body = json.dumps(
            {
                "model": self._model,
                "messages": [{"role": "user", "content": task.prompt}],
                "max_tokens": task.budget.max_tokens or 1024,
                "stream": False,
            }
        ).encode()
        request = urllib.request.Request(
            f"{self._base}/v1/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read())
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            return AgentResult(ok=False, error=f"api call failed: {exc}")
        choices = payload.get("choices") or []
        text = ((choices[0] or {}).get("message") or {}).get("content") if choices else ""
        if not isinstance(text, str):
            return AgentResult(ok=False, error="no text in the API response")
        try:
            output = _validate(parse_json_object(text), task)
        except HarnessError as exc:
            return AgentResult(ok=False, raw_tail=text[-4000:], error=str(exc))
        return AgentResult(ok=True, output=output, raw_tail=text[-4000:], usage=Usage())
