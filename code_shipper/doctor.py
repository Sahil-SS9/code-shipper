"""`code-shipper doctor`: report what is actually usable, per backend.

The point of this command is to make an honest three-way distinction and to be the thing a safety
setting hangs off:

* **VERIFIED** — the backend has been measured on this machine and passed its controls
* **UNVERIFIED** — present, but not measured, so nothing may depend on it
* **UNSUPPORTED** — not available here, or known not to provide the control

`allow_agent_approval` requires VERIFIED for the container, so a backend that quietly reports
UNVERIFIED keeps a safety setting off rather than on. That is the direction to fail in.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

VERIFIED = "VERIFIED"
UNVERIFIED = "UNVERIFIED"
UNSUPPORTED = "UNSUPPORTED"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    evidence: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "detail": self.detail,
                "evidence": self.evidence}


@dataclass
class Report:
    backends: dict[str, list[Check]] = field(default_factory=dict)
    harnesses: dict[str, list[Check]] = field(default_factory=dict)

    def overall(self, checks: list[Check]) -> str:
        """The weakest status wins: one UNSUPPORTED makes the whole backend unusable."""
        if not checks:
            return UNSUPPORTED
        if any(c.status == UNSUPPORTED for c in checks):
            return UNSUPPORTED
        if any(c.status == UNVERIFIED for c in checks):
            return UNVERIFIED
        return VERIFIED

    def backend_status(self, name: str) -> str:
        return self.overall(self.backends.get(name, []))

    def container_verified(self) -> bool:
        """Whether a container backend is VERIFIED, which is what agent approval requires."""
        return any(
            self.backend_status(name) == VERIFIED
            for name in self.backends
            if name.startswith(("docker", "bubblewrap", "seatbelt"))
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "backends": {
                name: {"status": self.overall(checks), "checks": [c.as_dict() for c in checks]}
                for name, checks in self.backends.items()
            },
            "harnesses": {
                name: {"status": self.overall(checks), "checks": [c.as_dict() for c in checks]}
                for name, checks in self.harnesses.items()
            },
        }

    def render(self) -> str:
        lines: list[str] = []
        for group, items in (("container backends", self.backends), ("harnesses", self.harnesses)):
            lines.append(f"{group}:")
            if not items:
                lines.append("  (none detected)")
            for name, checks in items.items():
                lines.append(f"  {name}: {self.overall(checks)}")
                for check in checks:
                    suffix = f" — {check.detail}" if check.detail else ""
                    lines.append(f"    [{check.status}] {check.name}{suffix}")
        return "\n".join(lines)


def _run(argv: list[str], timeout: float = 10.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return proc.returncode, (proc.stdout or proc.stderr).strip()


def check_docker_rootless() -> list[Check]:
    """Rootless Docker, measured: the daemon must report the rootless security option.

    A Docker daemon running as root is a different, wider boundary and is not treated as equivalent.
    """
    checks: list[Check] = []
    if not shutil.which("docker"):
        return [Check("docker", UNSUPPORTED, "docker not on PATH")]

    code, context = _run(["docker", "context", "show"])
    checks.append(
        Check("context", VERIFIED if code == 0 and context.strip() == "rootless" else UNVERIFIED,
              context.strip() or "unknown", "docker context show")
    )

    code, options = _run(["docker", "info", "--format", "{{json .SecurityOptions}}"])
    rootless = code == 0 and "rootless" in options
    checks.append(
        Check("rootless security option", VERIFIED if rootless else UNSUPPORTED,
              options[:120] or "unavailable", "docker info --format SecurityOptions")
    )

    code, version = _run(["docker", "version", "--format", "{{.Server.Version}}"])
    checks.append(
        Check("daemon reachable", VERIFIED if code == 0 else UNSUPPORTED,
              version.strip() or "no daemon", "docker version")
    )
    return checks


def check_containment_controls() -> list[Check]:
    """Whether this platform can provide the credential-hiding controls at all.

    Windows has no equivalent of the Linux/Seatbelt controls this project relies on, so contained
    fixing there is UNSUPPORTED until someone measures it.
    """
    if sys.platform.startswith("win"):
        return [Check("platform", UNSUPPORTED,
                      "no built-in equivalent of the measured controls on Windows")]
    if sys.platform == "darwin":
        if shutil.which("sandbox-exec"):
            return [Check("platform", UNVERIFIED, "sandbox-exec present but this build is unmeasured")]
        return [Check("platform", UNSUPPORTED, "sandbox-exec not available")]
    if sys.platform.startswith("linux"):
        if os.geteuid() == 0:
            return [Check("platform", UNVERIFIED,
                          "running as root, so the rootless control cannot be assessed")]
        return [Check("platform", VERIFIED, "linux with rootless containers available")]
    return [Check("platform", UNSUPPORTED, f"unknown platform {sys.platform}")]


def check_harness(name: str) -> list[Check]:
    from .harness.adapters import CommandHarness, HermesHarness

    if name == "hermes":
        ok, reason = HermesHarness().available()
        return [Check("hermes CLI", VERIFIED if ok else UNSUPPORTED, reason)]

    if name == "command":
        checks: list[Check] = []
        for preset in ("claude", "codex", "opencode"):
            harness = CommandHarness(preset=preset)
            ok, reason = harness.available()
            if not ok:
                checks.append(Check(preset, UNSUPPORTED, reason))
            else:
                # present but its preset has not passed a contract test on this machine
                checks.append(Check(preset, UNVERIFIED, reason))
        return checks

    if name == "openai_compat":
        key = os.environ.get("CODE_SHIPPER_API_KEY") or os.environ.get("OPENAI_API_KEY")
        return [
            Check("api key", VERIFIED if key else UNSUPPORTED,
                  "configured" if key else "no API key in the environment")
        ]

    return [Check(name, UNSUPPORTED, f"unknown harness {name!r}")]


def env_audit(env: dict[str, str] | None = None, *, home: str | None = None) -> list[Check]:
    """T-ADV-07: the environment the agent would inherit must carry no GitHub credentials.

    This audits the *current* process. It is deliberately conservative: a host shell that holds a
    GitHub token reports UNSUPPORTED here, because that is exactly the environment an uncontained
    run would inherit. A contained run is audited separately by the container canaries, and passing
    those does not make this check pass — they are different questions.

    Path checks only: no credential file is opened.
    """
    source = os.environ if env is None else env
    home_dir = home or source.get("HOME") or os.path.expanduser("~")
    checks: list[Check] = []

    leaked = sorted(
        key
        for key in source
        if key.upper().startswith(("GH_", "GITHUB_"))
        or key.upper().endswith(("_TOKEN", "_KEY", "_SECRET", "_PASSWORD"))
    )
    checks.append(
        Check("environment", VERIFIED if not leaked else UNSUPPORTED,
              "clean" if not leaked else f"credential-shaped variables: {leaked}")
    )

    for label, path in (
        ("git credentials file", os.path.join(home_dir, ".git-credentials")),
        ("gh hosts file", os.path.join(home_dir, ".config", "gh", "hosts.yml")),
        ("hermes env", os.path.join(home_dir, ".hermes", ".env")),
    ):
        exists = os.path.exists(path)
        checks.append(
            Check(label, VERIFIED if not exists else UNSUPPORTED,
                  "absent" if not exists else f"present at {path}")
        )

    return checks


def diagnose(*, env: dict[str, str] | None = None) -> Report:
    """Run every check and return the report. Never raises: a failing check is a status."""
    report = Report()
    report.backends["docker-rootless"] = check_docker_rootless() + check_containment_controls()
    report.backends["bubblewrap"] = [
        Check(
            "bwrap",
            # Present is not measured. Phase 0 measured rootless Docker; bwrap was blocked on this
            # machine and its controls have never been exercised here, so it cannot be VERIFIED.
            UNVERIFIED if shutil.which("bwrap") else UNSUPPORTED,
            (shutil.which("bwrap") or "bwrap not on PATH")
            + ("" if not shutil.which("bwrap") else " (present, controls unmeasured)"),
        )
    ]
    for name in ("hermes", "command", "openai_compat"):
        report.harnesses[name] = check_harness(name)
    report.harnesses["environment"] = env_audit(env)
    return report
