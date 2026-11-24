"""CP4 worker: one contained fix run, inside the container.

Hermes runs with the `file` toolset only — no terminal, no web, no network. It edits the copy of the
repo at /work/repo and nothing else. It never runs git and never pushes: the broker does that, on the
host, after checking the diff is in scope.

The worker does not decide whether the fix is good. It reports what changed and lets the host check
it. If Hermes declines or produces nothing, that is a result, not a crash.

Outputs, all in /work:
  fix-result.json     the harness result, both counters, the adapter log tail
  changed.json        the list of changed files relative to /work/repo
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

WORK = Path("/work")
REPO = WORK / "repo"
HERMES_HOME = WORK / "hermes-home" / "home"
ADAPTER_PORT = 8111
FIX_TIMEOUT_S = 240
MAX_TURNS = 8

PROMPT = """You are fixing a failing test in a small Python project.

The current directory is the project. One test fails because the code has a bug.

Rules:
- Fix the bug in the source code. Do NOT change, weaken, delete or skip any test.
- Edit files only. Do not run git, gh, pip, curl or any network command.
- Only touch files under calc/ or tests/.
- When you are done, reply with a single JSON object and nothing else:
  {{"status": "changed|no_change|declined", "summary": "<one sentence>"}}

The failing test output:

<untrusted source="ci_log">
{failure}
</untrusted>
"""


def main() -> int:
    task = json.loads((WORK / "task.json").read_text(encoding="utf-8"))
    result: dict[str, object] = {}

    delay = task.get("delay_s")
    if delay:
        time.sleep(float(delay))

    HERMES_HOME.mkdir(parents=True, exist_ok=False)
    if HERMES_HOME.stat().st_uid != os.getuid():
        print("fix-run: HERMES_HOME is not owned by this user", file=sys.stderr)
        return 6
    write_config()

    adapter = start_adapter()
    try:
        if not wait_for_adapter(deadline=20.0):
            print("fix-run: adapter never became ready", file=sys.stderr)
            return 4

        before = snapshot()
        prompt = PROMPT.format(failure=task.get("failure", "(no output)"))
        hermes = run_hermes(prompt, timeout=task.get("timeout_s", FIX_TIMEOUT_S))
        after = snapshot()

        result["hermes"] = hermes
        result["changed"] = sorted(
            path for path, digest in after.items() if before.get(path) != digest
        )
        result["removed"] = sorted(path for path in before if path not in after)
        result["adapter_calls"] = read_counter("calls")
        result["adapter_refused"] = read_counter("refused")
        result["adapter_log_tail"] = read_adapter_log()
        (WORK / "fix-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (WORK / "changed.json").write_text(
            json.dumps({"changed": result["changed"], "removed": result["removed"]}, indent=2),
            encoding="utf-8",
        )

        print(
            f"fix-run: exit {hermes['returncode']}, changed {len(result['changed'])}, "
            f"completions {result['adapter_calls']}, refused {result['adapter_refused']}"
        )
        return 0
    finally:
        stop_adapter(adapter)


def snapshot() -> dict[str, str]:
    """Hash every file in the repo copy, so a change is detected however Hermes made it."""
    import hashlib

    digests: dict[str, str] = {}
    for path in REPO.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            try:
                digests[str(path.relative_to(REPO))] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
    return digests


def write_config() -> None:
    config = f"""model: auto
provider: csbroker
providers:
  csbroker:
    type: openai_compat
    base_url: http://127.0.0.1:{ADAPTER_PORT}/v1
    api_key_env: CS_PLACEHOLDER_KEY
    models:
      - auto
agent:
  max_turns: {MAX_TURNS}
"""
    (HERMES_HOME / "config.yaml").write_text(config, encoding="utf-8")
    (HERMES_HOME / ".env").write_text(
        "CS_PLACEHOLDER_KEY=placeholder-not-a-credential\n", encoding="utf-8"
    )
    os.chmod(HERMES_HOME / ".env", 0o600)
    disable_tools_except_file()


# Everything except `file`. `-t file` alone does not achieve that: Hermes adds its default toolsets
# on top, which hands the worker a terminal, a browser and a network surface it must not have.
TOOLSETS_EXCEPT_FILE = (
    "web", "browser", "terminal", "code_execution", "vision", "image_gen", "tts",
    "skills", "todo", "memory", "session_search", "connections", "clarify", "delegation",
    "cronjob", "computer_use",
)


def disable_tools_except_file() -> None:
    """Leave only the file toolset enabled, so the worker can edit but not reach anything else."""
    subprocess.run(
        ["hermes", "tools", "disable", *TOOLSETS_EXCEPT_FILE],
        capture_output=True, text=True, timeout=120,
        env={
            "PATH": "/opt/hermes/bin:/opt/hermes/.venv/bin:/usr/bin:/bin",
            "HOME": str(HERMES_HOME),
            "HERMES_HOME": str(HERMES_HOME),
            "NO_COLOR": "1",
        },
    )


def start_adapter() -> subprocess.Popen:
    log = open(WORK / "adapter.log", "w", encoding="utf-8")
    return subprocess.Popen(
        [sys.executable, "/app/adapter.py", str(ADAPTER_PORT)],
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
        cwd="/app",
    )


def stop_adapter(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def wait_for_adapter(*, deadline: float) -> bool:
    end = time.time() + deadline
    while time.time() < end:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{ADAPTER_PORT}/v1/models", timeout=2
            ) as response:
                if response.status == 200:
                    return True
        except OSError:
            time.sleep(0.2)
    return False


def read_counter(kind: str) -> int:
    path = WORK / f"adapter-{kind}.json"
    if not path.exists():
        return 0
    try:
        return int(json.loads(path.read_text(encoding="utf-8"))[kind])
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return -1


def read_adapter_log(*, tail: int = 4000) -> str:
    try:
        return (WORK / "adapter.log").read_text(encoding="utf-8", errors="replace")[-tail:]
    except OSError:
        return ""


def run_hermes(prompt: str, *, timeout: float) -> dict:
    """One bounded fix session. Our wall clock, our kill: the harness will not stop itself."""
    env = {
        "PATH": "/opt/hermes/bin:/opt/hermes/.venv/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(HERMES_HOME),
        "HERMES_HOME": str(HERMES_HOME),
        "LANG": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "TMPDIR": "/tmp",
        "NO_COLOR": "1",
    }
    argv = [
        "hermes", "chat",
        "--query-file", "-",
        "--oneshot",
        "-Q",
        "--provider", "csbroker",
        "--model", "auto",
        "-t", "file",                    # file edits only: no terminal, no web, no network tools
        "--max-turns", str(MAX_TURNS),
        "--run-budget", str(FIX_TIMEOUT_S),
        "--source", "tool",
    ]
    started = time.time()
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO),
        env=env,
        text=True,
    )
    killed = False
    try:
        out, err = proc.communicate(input=prompt, timeout=timeout)
        returncode = proc.returncode
    except subprocess.TimeoutExpired:
        killed = True
        proc.kill()
        out, err = proc.communicate()
        returncode = proc.returncode if proc.returncode is not None else -9

    return {
        "argv": argv,
        "returncode": returncode,
        "seconds": round(time.time() - started, 2),
        "timed_out": killed,
        "timeout_s": timeout,
        "stdout": (out or "")[-4000:],
        "stderr": (err or "")[-4000:],
    }


if __name__ == "__main__":
    raise SystemExit(main())
