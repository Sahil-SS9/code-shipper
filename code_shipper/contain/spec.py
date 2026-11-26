"""Container runtime spec for a contained worker (§6.1, tech spec).

This module is the single definition of the docker arguments. Nothing else builds a container, so
there is one place to audit when the boundary changes.

Transport decision: the worker gets **no network at all** (``--network none``) and reaches the
broker through a unix socket in a bind-mounted directory. A dedicated bridge network would have
needed an IP endpoint, a listener bound to it and a rule limiting egress to that endpoint; a
socket mount needs none of that, and the worker still has no route anywhere. The socket directory
is the only writable host path besides the workspace.

Mounts, all explicit:
  /source    read-only   the checkout under repair
  /work      read-write  the workspace the worker may edit
  /app       read-only   the Code-Shipper worker code (no credentials, no host paths)
  /run/cs    read-only   one socket directory holding the broker socket
  /tmp       tmpfs       noexec,nosuid
No host HOME, no credential store, no docker or system-bus socket.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Deliberately not configurable per run: these are the properties the proof depends on.
WORKER_UID = "65534:65534"  # nobody; the container is already a userns, this is defence in depth
BROKER_MOUNT = "/run/cs"
APP_MOUNT = "/app"


@dataclass(frozen=True)
class ContainerSpec:
    """A fully resolved container invocation. Deterministic, so it can be hashed and compared."""

    image: str
    source_dir: Path
    work_dir: Path
    broker_dir: Path | None
    entrypoint: str
    app_dir: Path | None = None
    command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    pids_limit: int = 64
    memory: str = "512m"
    cpus: str = "1"
    # Hermes writes a full HERMES_HOME: skills, caches, sessions, sqlite state. On a bind mount that
    # leaves thousands of files owned by uid 65534 that the host user cannot delete afterwards, and
    # on --read-only it fails outright. A tmpfs gives it somewhere disposable to write.
    tmpfs: tuple[str, ...] = (
        "/tmp:rw,noexec,nosuid,size=256m",
        "/work/hermes-home:rw,nosuid,size=512m,mode=1777",
    )

    def to_argv(self, *, container_name: str, docker_context: str = "rootless") -> list[str]:
        argv = [
            "docker", "--context", docker_context, "run",
            "--rm",
            "--name", container_name,
            "--pull", "never",             # no downloads during a run
            "--network", "none",           # no egress at all; the socket is the only channel
            "--read-only",                 # root filesystem cannot be modified
            "--user", WORKER_UID,
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", str(self.pids_limit),
            "--memory", self.memory,
            "--cpus", self.cpus,
        ]
        for entry in self.tmpfs:
            argv += ["--tmpfs", entry]
        argv += [
            "--mount", f"type=bind,src={self.source_dir},dst=/source,readonly",
            "--mount", f"type=bind,src={self.work_dir},dst=/work",
        ]
        if self.app_dir is not None:
            argv += ["--mount", f"type=bind,src={self.app_dir},dst={APP_MOUNT},readonly"]
        if self.broker_dir is not None:
            argv += ["--mount", f"type=bind,src={self.broker_dir},dst={BROKER_MOUNT},readonly"]
        for key, value in sorted(self.env.items()):
            argv += ["--env", f"{key}={value}"]
        argv += ["--entrypoint", self.entrypoint, self.image, *self.command]
        return argv

    def describe(self) -> dict[str, object]:
        """The policy, as data, for the report and the receipt."""
        mounts = {
            "/source": {"src": str(self.source_dir), "mode": "ro"},
            "/work": {"src": str(self.work_dir), "mode": "rw"},
        }
        if self.app_dir is not None:
            mounts[APP_MOUNT] = {"src": str(self.app_dir), "mode": "ro"}
        if self.broker_dir is not None:
            mounts[BROKER_MOUNT] = {"src": str(self.broker_dir), "mode": "ro"}
        return {
            "image": self.image,
            "network": "none",
            "root_filesystem": "read-only",
            "user": WORKER_UID,
            "capabilities": "ALL dropped",
            "no_new_privileges": True,
            "pids_limit": self.pids_limit,
            "memory": self.memory,
            "cpus": self.cpus,
            "tmpfs": list(self.tmpfs),
            "mounts": mounts,
            "host_home": None,
            "docker_socket": None,
        }

    def validate(self) -> None:
        """Refuse a spec that would silently weaken the boundary."""
        problems: list[str] = []
        if self.env:
            problems.append("env must be empty: worker configuration arrives via files, not env")
        if not self.image:
            problems.append("image is required")
        if not self.source_dir.is_absolute() or not self.work_dir.is_absolute():
            problems.append("mount sources must be absolute paths")
        for optional in (self.broker_dir, self.app_dir):
            if optional is not None and not optional.is_absolute():
                problems.append("optional mount sources must be absolute paths")
        for path in (self.source_dir, self.work_dir, self.app_dir, self.broker_dir):
            if path is None:
                continue
            reason = credential_location(str(path))
            if reason:
                problems.append(
                    f"refusing to mount {path}: {reason}. "
                    f"These directories hold credentials and must never be mounted into a worker. "
                    f"Run from a checkout outside them, for example ~/repos/<name>."
                )
        if not self.entrypoint.startswith("/"):
            problems.append("entrypoint must be an absolute path inside the image")

        # Note: work_dir is intentionally world-writable, because the worker runs as a different uid
        # inside the container and must edit it. What must be private is the *run directory* holding
        # the broker socket, and that is enforced by `workspace.require_private_dir` at creation and
        # again immediately before bind. Checking work_dir's mode here would refuse every real run.

        if problems:
            raise ValueError("; ".join(problems))


# What actually holds credentials, as opposed to anything that merely lives under a directory whose name
# starts with a dot. The old rule refused every path containing "/.hermes", which meant a checkout at
# ~/.hermes/repos/x could not run a review at all — a guard that blocks the tool's own users is a guard
# that gets removed. It also refused "/.config", which is where an ordinary Linux checkout never sits but
# a vendored config directory often does.
CREDENTIAL_DIRS = (".ssh", ".aws", ".gnupg", ".docker", ".kube", ".password-store")

# Inside a home directory these hold keys, tokens or session state. The names are exact rather than
# prefix-matched: `.hermes` on its own is the live install and is refused, while `.hermes/cache` is not.
CREDENTIAL_FILES = (
    ".hermes/auth.json", ".hermes/auth", ".hermes/config.yaml", ".hermes/.env",
    ".netrc", ".git-credentials", ".npmrc", ".pypirc", ".env",
)

# Credential *directories* inside the agent's own home. Anything else under it — caches, logs, a checkout —
# is ordinary filesystem and may be mounted.
HERMES_CREDENTIAL_DIRS = ("auth", "tokens", "secrets", "credentials", "profiles")

# Tool config directories that hold a token rather than preferences. `.config` itself is ordinary; these
# two are not.
CREDENTIAL_CONFIG_DIRS = (("gh",), ("gcloud",), ("hub",), ("glab",))


def credential_location(path_text: str) -> str:
    """Why this path must not be mounted, or '' if it is fine.

    Deliberately narrower than "any path with a dot directory in it": the rule exists to keep credentials
    out of a worker, not to make the tool refuse to run. A checkout under `~/.hermes/repos/` is not a
    credential and is now allowed.
    """
    text = path_text.replace("\\", "/")
    lowered = text.lower()
    parts = [part for part in lowered.split("/") if part]

    for needle in CREDENTIAL_DIRS:
        # Match a whole path component, so ".sshfoo" is not refused and "id_rsa" under .ssh is.
        if any(part == needle for part in parts):
            return f"it is inside {needle}, which holds credentials"

    for tail in CREDENTIAL_FILES:
        if lowered.endswith(tail) or f"/{tail}/" in lowered + "/":
            return f"it is {tail}, which holds a credential"

    for index, part in enumerate(parts[:-1]):
        if part != ".config":
            continue
        if (parts[index + 1],) in CREDENTIAL_CONFIG_DIRS:
            return f"it is inside .config/{parts[index + 1]}, which holds an access token"

    # A path *through* .hermes/auth (or tokens, secrets...) is a credential store. A path merely under
    # ~/.hermes is not.
    for index, part in enumerate(parts[:-1]):
        if part != ".hermes":
            continue
        following = parts[index + 1]
        if following in HERMES_CREDENTIAL_DIRS:
            return f"it is inside .hermes/{following}, which holds credentials"

    if lowered.rstrip("/").endswith("/.hermes") or lowered.rstrip("/") == ".hermes":
        return "it is the live Hermes home, which holds credentials and session state"

    return ""


def workspace_paths(root: Path) -> tuple[Path, Path]:
    """Create the read-only source and the writable workspace under ``root``.

    The workspace is a copy, not the checkout: the worker edits a directory the broker created
    for this run, so it cannot touch the user's repository or its .git directory.
    """
    source = root / "source"
    work = root / "work"
    source.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    os.chmod(work, 0o777)  # the worker runs as 65534 and must be able to write here
    os.chmod(source, 0o755)
    return source, work
