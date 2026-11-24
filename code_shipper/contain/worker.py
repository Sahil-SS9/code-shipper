"""The in-container worker entry point.

This file runs INSIDE the container. It may not assume anything is installed beyond the Python
standard library, and it must contain no credential, no provider URL and no host path. It reads a
task from /work/task.json, asks the broker for a completion, and writes the result to
/work/result.json.

For CP2 the "agent" is deliberately a stand-in: it makes one broker call, writes what it got, and
exits. The point of CP2 is the container and the channel, not the model. CP3 swaps the stand-in
for a real harness driving the same client.

If the broker is unreachable or refuses, the worker exits non-zero and writes no result. It never
substitutes a canned answer, and there is no fallback path to a provider: it holds no key.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

WORK = Path("/work")
RESULT = WORK / "result.json"
CAPABILITY_FILE = WORK / "capability"


def main() -> int:
    task = json.loads((WORK / "task.json").read_text(encoding="utf-8"))
    broker = json.loads((WORK / "broker.json").read_text(encoding="utf-8"))
    capability = CAPABILITY_FILE.read_text(encoding="utf-8").strip()

    delay = task.get("delay_s")
    if delay:
        # Only used by the fail-closed probe: gives the host time to stop the broker mid-run.
        import time

        time.sleep(float(delay))

    payload = {
        "op": "complete",
        "capability": capability,
        "provider": broker["provider"],
        "model": broker["model"],
        "messages": [{"role": "user", "content": task["prompt"]}],
        "max_tokens": task.get("max_tokens", 256),
    }

    try:
        response = _call(broker["socket"], payload, timeout=broker.get("timeout", 120))
    except OSError as exc:
        # Fail closed: no completion, no result file, non-zero exit.
        print(f"worker: broker unreachable: {exc}", file=sys.stderr)
        return 2

    if not response.get("ok"):
        print(f"worker: broker refused: {response.get('error')}", file=sys.stderr)
        return 3

    RESULT.write_text(
        json.dumps(
            {
                "text": response["text"],
                "usage": response.get("usage"),
                "uid": os.getuid(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("worker: completed")
    return 0


def _call(socket_path: str, payload: dict, *, timeout: float) -> dict:
    import socket

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(socket_path)
    try:
        sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        raw = b""
        while b"\n" not in raw:
            chunk = sock.recv(4096)
            if not chunk:
                break
            raw += chunk
    finally:
        sock.close()
    if not raw:
        raise OSError("broker closed the connection without a response")
    return json.loads(raw.split(b"\n", 1)[0])


if __name__ == "__main__":
    raise SystemExit(main())
