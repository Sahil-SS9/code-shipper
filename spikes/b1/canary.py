"""Canary checks for a contained worker, run INSIDE the container.

Same idea as the M0 probe: prove the negative controls (what must be absent) and the benign
controls (what must still work, so "passed" cannot mean "denied everything"). Uses synthetic canary
files only; it never opens a real credential.

Host facts arrive as a FILE (``/work/host-probe.json``), not as environment variables, because the
container spec forbids environment variables outright. That is also why the environment check below
looks for leaked host credentials by name rather than by diffing against the parent environment:
there is no parent environment to diff against.

  /work/canary.json  the machine-readable result
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

WORK = Path("/work")
SOURCE = Path("/source")
HOST_PROBE = WORK / "host-probe.json"
results: list[tuple[str, bool, str]] = []

# Names that would mean a credential reached the container. Compared exactly, so a base image
# variable such as GPG_KEY (a public build key, present in the Python image) is not mistaken for
# a leaked secret.
SENSITIVE_ENV = {
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GITHUB_PERSONAL_ACCESS_TOKEN",
    "SSH_AUTH_SOCK",
    "DBUS_SESSION_BUS_ADDRESS",
    "DOCKER_HOST",
    "HERMES_SESSION_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
}
SENSITIVE_PREFIXES = ("GH_", "GITHUB_")


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} {name}{(' [' + detail + ']') if detail else ''}")


def exists(path: str) -> bool:
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def readable(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            handle.read(1)
        return True
    except OSError:
        return False


def main() -> int:
    env = os.environ
    host = json.loads(HOST_PROBE.read_text(encoding="utf-8")) if HOST_PROBE.exists() else {}

    # --- benign controls: the worker must be able to do its job
    check("source mounted read-only and readable", SOURCE.is_dir() and readable(str(SOURCE / "fixture.txt")))
    check("source file content readable", readable(str(SOURCE / "fixture.txt")))
    check("workspace writable", _try_write(WORK / "probe-write.txt"))
    check("tmp writable", _try_write(Path("/tmp/probe.tmp")))
    # a canary deliberately mounted so "absent" is not confused with "nothing works"
    check("positive control: mounted canary readable", readable("/source/visible-canary.txt"))

    # --- negative controls: credential paths must be absent
    host_home = host.get("host_home", "/root")
    for path in (
        f"{host_home}/.hermes/.env",
        f"{host_home}/.config/gh/hosts.yml",
        f"{host_home}/.git-credentials",
        f"{host_home}/.ssh/id_rsa",
        f"{host_home}/.config/code-shipper/tokens/fixer",
    ):
        check(f"absent: {path}", not exists(path))

    # canaries the host wrote outside every mount: reachable only if a mount is larger than intended
    for key, label in (
        ("outside_canary", "outside canary"),
        ("outside_fake_gh", "outside fake gh config"),
        ("outside_fake_fixer", "outside fake fixer token"),
    ):
        path = host.get(key)
        if path:
            check(f"absent: {label} ({path})", not exists(path))

    # --- no credential material in the environment
    leaked = sorted(
        key
        for key in env
        if key in SENSITIVE_ENV or key.startswith(SENSITIVE_PREFIXES) or key.endswith(("_TOKEN", "_SECRET"))
    )
    check("no credential-shaped environment variables", not leaked, ",".join(leaked))
    check("environment carries no host paths", not any("kensei" in v for v in env.values()))

    # --- no container escape surfaces
    check("no docker socket", not exists("/var/run/docker.sock"))
    check("no system bus socket", not exists("/run/dbus/system_bus_socket"))
    host_pid = str(host.get("host_pid", ""))
    check(
        "host process is not visible",
        bool(host_pid) and not exists(f"/proc/{host_pid}"),
        f"host_pid={host_pid or 'unknown'}",
    )

    # --- filesystem: root and source are read-only
    check("root filesystem not writable", not _try_write(Path("/opt/canary-root-write")))
    check("source not writable", not _try_write(SOURCE / "write-attempt.txt"))

    # --- privileges
    check("uid is not root", os.getuid() != 0, f"uid={os.getuid()}")
    check("no-new-privileges effective", _no_new_privs())
    check("capabilities empty", _capabilities_empty())

    # --- network
    check("network namespace is not the host's", _netns_distinct(host.get("host_net_ns", "")))
    check("no non-loopback route", not _has_route())

    # --- the only channel out is the broker socket
    broker_socket = ""
    broker_file = WORK / "broker.json"
    if broker_file.exists():
        broker_socket = json.loads(broker_file.read_text(encoding="utf-8")).get("socket", "")
    check("broker socket present", bool(broker_socket) and exists(broker_socket), broker_socket or "not configured")
    check(
        "no credential file beside the socket",
        not any(
            (Path(broker_socket).parent / name).exists()
            for name in ("provider.key", "token", "api-key")
        )
        if broker_socket
        else True,
    )

    failed = [name for name, ok, _ in results if not ok]
    payload = {
        "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in results],
        "passed": sum(1 for _, ok, _ in results if ok),
        "failed": failed,
    }
    try:
        (WORK / "canary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass
    print("DONE")
    return 0 if not failed else 1


def _try_write(path: Path) -> bool:
    try:
        path.write_text("x", encoding="utf-8")
        return True
    except OSError:
        return False


def _no_new_privs() -> bool:
    try:
        return "NoNewPrivs:\t1" in Path("/proc/self/status").read_text()
    except OSError:
        return False


def _capabilities_empty() -> bool:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("CapEff:"):
                return int(line.split()[1], 16) == 0
    except (OSError, ValueError):
        return False
    return False


def _netns_distinct(host_net_ns: str) -> bool:
    if not host_net_ns:
        return False
    try:
        return os.readlink("/proc/self/ns/net") != host_net_ns
    except OSError:
        return True


def _has_route() -> bool:
    try:
        out = subprocess.run(["ip", "route"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            return any(
                line and not line.startswith("127.") and "lo" not in line.split()
                for line in out.stdout.splitlines()
            )
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        for line in Path("/proc/net/route").read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) > 2 and fields[1] != "00000000":
                return True
    except OSError:
        pass
    return False


if __name__ == "__main__":
    sys.exit(main())
