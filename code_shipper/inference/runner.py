"""Start the broker as a standalone host process.

The credential is read from a file (never an environment variable, so it cannot be inherited by
a child process or shown by ``docker inspect``). The capability token is written to a file with
mode 0600 for the run harness to hand to the worker; it is printed nowhere.

    python3 -m code_shipper.inference.runner \
        --socket /run/cs/broker.sock --ledger /run/cs/receipts.jsonl \
        --provider local --model auto \
        --upstream-url http://127.0.0.1:8091 --credential-file /path/to/key \
        --capability-out /run/cs/capability --requests 4

Exits non-zero without creating a socket if anything required is missing: a broker that starts
in a half-configured state would be a broker that fails open.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import stat
import sys
import time
from pathlib import Path

from .broker import Broker, StartupError, read_secret_file
from .upstream import OpenAICompatUpstream


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="code-shipper-inference-broker")
    parser.add_argument("--socket", required=True, help="AF_UNIX path the worker connects to")
    parser.add_argument("--ledger", required=True, help="receipt JSON-lines file")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--upstream-url", default="", help="OpenAI-compatible base URL")
    parser.add_argument("--credential-file", default="", help="file holding the provider key")
    parser.add_argument("--capability-out", required=True, help="file to write the run capability to")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--requests", type=int, default=8)
    parser.add_argument("--prompt-chars", type=int, default=24000)
    parser.add_argument("--max-output-tokens", type=int, default=1024)
    parser.add_argument("--ttl", type=float, default=3600.0)
    parser.add_argument("--socket-mode", default="0600", help="octal mode for the socket file")
    parser.add_argument("--ready-file", default="", help="written after the socket is listening")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    mode = int(args.socket_mode, 8)

    if bool(args.upstream_url) != bool(args.credential_file):
        print("broker: --upstream-url and --credential-file must be given together", file=sys.stderr)
        return 2
    try:
        api_key = read_secret_file(args.credential_file) if args.credential_file else ""
        if not args.upstream_url:
            raise StartupError("no upstream configured")
        upstream = OpenAICompatUpstream(args.upstream_url, api_key, required=False)
        broker = Broker(upstream, ledger_path=args.ledger)
    except StartupError as exc:
        print(f"broker: refusing to start: {exc}", file=sys.stderr)
        return 2

    run_id = args.run_id or f"run-{int(time.time())}"
    try:
        cap = broker.issue_capability(
            run_id=run_id,
            provider=args.provider,
            model=args.model,
            max_requests=args.requests,
            max_prompt_chars=args.prompt_chars,
            max_output_tokens=args.max_output_tokens,
            ttl_seconds=args.ttl,
        )
    except ValueError as exc:
        print(f"broker: refusing to start: {exc}", file=sys.stderr)
        return 2

    cap_path = Path(args.capability_out)
    cap_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(cap_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(cap.token + "\n")

    try:
        server = broker.serve_socket(args.socket, mode=mode)
    except (StartupError, OSError) as exc:
        print(f"broker: cannot listen: {exc}", file=sys.stderr)
        return 3

    stopping: dict[str, int | None] = {"signal": None}

    def _stop(signum: int, _frame: object) -> None:
        stopping["signal"] = signum

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    server.start()
    part = cap.public()
    part.pop("expires_at", None)
    print(json.dumps({"listening": args.socket, "capability": part}, sort_keys=True), flush=True)

    if args.ready_file:
        Path(args.ready_file).write_text("ready\n", encoding="utf-8")

    try:
        while stopping["signal"] is None and broker.failed is None:
            time.sleep(0.1)
    finally:
        server.stop()
        broker.revoke(cap.token)
        try:
            cap_path.unlink()
        except OSError:
            pass

    if broker.failed:
        print(f"broker: stopped after failure: {broker.failed}", file=sys.stderr)
        return 4
    print(json.dumps({"stopped": True, "served": server.served}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
