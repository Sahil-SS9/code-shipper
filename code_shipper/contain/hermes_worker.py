"""Run one real Hermes session inside the container, through the adapter, through the broker.

This runs INSIDE the container as uid 65534. It does not hold a credential: Hermes is configured
with a custom provider whose base URL is the loopback adapter, and whose API key is a literal
placeholder the adapter discards.

Sequence:
  1. start the adapter on 127.0.0.1:PORT, pointing at the broker socket
  2. write a minimal config.yaml and .env into a FRESH, empty HERMES_HOME under /work
  3. wait for the adapter to answer /v1/models
  4. run one bounded `hermes chat` one-shot: tiny prompt, no toolsets
  5. record what happened, including how many adapter calls were made

Never uses the image's /root/.hermes. HERMES_HOME is created empty for this run alone.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, "/app")

WORK = Path("/work")
# Inside the tmpfs mount, so it is disposable and never litters the host with uid-65534 files.
HERMES_HOME = WORK / "hermes-home" / "home"
ADAPTER_PORT = 8111
MAX_TURNS = 2
RUN_BUDGET_S = 120

# The one prompt. Tiny on purpose: this proves the path, not the model's ability.
PROMPT = "Reply with exactly one word: pong. No punctuation, no explanation."


def main() -> int:
    task = json.loads((WORK / "task.json").read_text(encoding="utf-8"))
    prompt = task.get("prompt", PROMPT)
    result: dict[str, object] = {"prompt": prompt}

    # Only used by the fail-closed probe: hold here after the canaries and before Hermes starts, so
    # the host has time to stop the broker while this run is genuinely in flight.
    delay = task.get("delay_s")
    if delay:
        import time

        time.sleep(float(delay))

    # A fresh HERMES_HOME. On tmpfs, because Hermes writes skills, caches, sessions and sqlite
    # state; on the bind-mounted workspace those files would be owned by uid 65534 and the host
    # user could not remove them afterwards.
    #
    # HERMES_HOME is a *subdirectory* of the tmpfs mount, not the mount point: the mount point is
    # root-owned and a non-root process cannot chown it, and Hermes refuses a control path it does
    # not own. The tmpfs itself is mode 1777, so uid 65534 can create its own directory inside.
    HERMES_HOME.mkdir(parents=True, exist_ok=False)
    if HERMES_HOME.stat().st_uid != os.getuid():
        print("hermes-run: HERMES_HOME is not owned by this user", file=sys.stderr)
        return 6
    write_config()

    adapter_proc = start_adapter()
    try:
        if not wait_for_adapter(deadline=20.0):
            print("hermes-run: adapter never became ready", file=sys.stderr)
            return 4

        hermes = run_hermes(prompt, timeout=task.get("timeout_s", RUN_BUDGET_S + 60))
        result["hermes"] = hermes
        # Written before the adapter is stopped: a hard stop must not be able to lose the evidence.
        result["adapter_calls"] = read_counter("calls")
        result["adapter_refused"] = read_counter("refused")
        result["adapter_log_tail"] = read_adapter_log()
        (WORK / "hermes-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

        print(
            f"hermes-run: exit {hermes['returncode']}, "
            f"completions {result['adapter_calls']}, refused {result['adapter_refused']}"
        )
        return 0 if hermes["returncode"] == 0 else 5
    finally:
        stop_adapter(adapter_proc)


def write_config() -> None:
    """Minimal config. A custom provider pointing at loopback; a placeholder key, not a credential."""
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
    # The placeholder exists so Hermes's provider config resolves. It authorises nothing: the
    # adapter ignores it and the broker never sees it.
    (HERMES_HOME / ".env").write_text("CS_PLACEHOLDER_KEY=placeholder-not-a-credential\n", encoding="utf-8")
    os.chmod(HERMES_HOME / ".env", 0o600)


def start_adapter() -> subprocess.Popen:
    """Start the adapter with its output going to a file.

    A PIPE that is never drained can fill and block the adapter mid-run, and with STOPPED/PIPE the
    output is lost when the process is killed. A file survives both, so the refusal evidence is
    still there after a hard stop.
    """
    log = open(WORK / "adapter.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "/app/adapter.py", str(ADAPTER_PORT)],
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
        cwd="/app",
    )
    return proc


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
    """Read a counter the adapter wrote. Missing means zero, which is itself evidence."""
    path = WORK / f"adapter-{kind}.json"
    if not path.exists():
        return 0
    try:
        return int(json.loads(path.read_text(encoding="utf-8"))[kind])
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return -1


def read_adapter_log(*, tail: int = 4000) -> str:
    """The adapter's own output, including one line per refusal."""
    try:
        text = (WORK / "adapter.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-tail:]


def run_hermes(prompt: str, *, timeout: float) -> dict:
    """Run one bounded Hermes session. On timeout we kill it ourselves and still return evidence.

    Hermes ignores its own --run-budget when the provider keeps refusing: it retries for minutes.
    So the wall clock here is ours, and a timeout is a result to record, not an exception to raise.
    """
    env = {
        "PATH": "/opt/hermes/bin:/opt/hermes/.venv/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(WORK / "hermes-home"),
        "HERMES_HOME": str(HERMES_HOME),
        "LANG": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "TMPDIR": "/tmp",
        # No provider credential: the adapter needs none, and the broker holds the only real one.
        "NO_COLOR": "1",
    }
    argv = [
        "hermes", "chat",
        "--query-file", "-",
        "--oneshot",
        "-Q",
        "--provider", "csbroker",
        "--model", "auto",
        "-t", "",                       # no toolsets: matching and nothing else
        "--max-turns", str(MAX_TURNS),
        "--run-budget", str(RUN_BUDGET_S),
        "--source", "tool",
    ]
    started = time.time()
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(WORK),
        env=env,
        text=True,
    )
    killed = False
    try:
        out, err = proc.communicate(input=prompt, timeout=timeout)
        returncode = proc.returncode
    except subprocess.TimeoutExpired:
        # Our budget, not Hermes's. Kill it and record that we did.
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
