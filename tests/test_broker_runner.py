"""Tests for the standalone broker process: it must refuse to start rather than start unsafely."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Unix-domain sockets and POSIX file modes do not exist on Windows. These tests are
# skipped there rather than allowed to fail, so the CI matrix reports real coverage.
posix_only = unittest.skipIf(sys.platform.startswith("win"), "POSIX-only test")



def run_broker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "code_shipper.inference.runner", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PYTHONPATH": str(REPO)},
    )


@posix_only
class RefusesToStartTests(unittest.TestCase):
    def test_refuses_with_no_upstream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_broker(
                "--socket", f"{tmp}/b.sock",
                "--ledger", f"{tmp}/r.jsonl",
                "--provider", "local",
                "--model", "m",
                "--capability-out", f"{tmp}/cap",
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("refusing to start", result.stderr)
            self.assertFalse(Path(tmp, "b.sock").exists())
            self.assertFalse(Path(tmp, "cap").exists())

    def test_refuses_with_unreadable_credential(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_broker(
                "--socket", f"{tmp}/b.sock",
                "--ledger", f"{tmp}/r.jsonl",
                "--provider", "local",
                "--model", "m",
                "--upstream-url", "http://127.0.0.1:1",
                "--credential-file", f"{tmp}/missing-key",
                "--capability-out", f"{tmp}/cap",
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("credential file unreadable", result.stderr)
            self.assertFalse(Path(tmp, "b.sock").exists())

    def test_refuses_a_url_without_a_credential(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_broker(
                "--socket", f"{tmp}/b.sock",
                "--ledger", f"{tmp}/r.jsonl",
                "--provider", "local",
                "--model", "m",
                "--upstream-url", "http://127.0.0.1:1",
                "--capability-out", f"{tmp}/cap",
            )
            self.assertEqual(result.returncode, 2)


@posix_only
class ServesAndStopsTests(unittest.TestCase):
    def test_starts_serves_one_request_and_stops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sock_path = f"{tmp}/b.sock"
            ready = f"{tmp}/ready"
            key = Path(tmp) / "key"
            key.write_text("sk-fake-test-key\n")
            key.chmod(0o600)

            proc = subprocess.Popen(
                [
                    sys.executable, "-m", "code_shipper.inference.runner",
                    "--socket", sock_path,
                    "--ledger", f"{tmp}/r.jsonl",
                    "--provider", "fake",
                    "--model", "fake-model",
                    "--upstream-url", "http://127.0.0.1:1",
                    "--credential-file", str(key),
                    "--capability-out", f"{tmp}/cap",
                    "--ready-file", ready,
                    "--requests", "1",
                ],
                cwd=REPO,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "PYTHONPATH": str(REPO)},
            )
            try:
                deadline = time.time() + 20
                while not Path(ready).exists() and time.time() < deadline:
                    self.assertIsNone(proc.poll(), "broker exited before becoming ready")
                    time.sleep(0.05)
                self.assertTrue(Path(ready).exists(), "broker never became ready")

                cap = Path(tmp, "cap").read_text().strip()
                self.assertTrue(cap)
                self.assertEqual(stat_mode(Path(tmp, "cap")), 0o600)

                # The upstream is a dead port, so the honest answer is upstream_error, not text.
                response = one_request(sock_path, cap)
                self.assertFalse(response["ok"])
                self.assertEqual(response["error"], "upstream_error")

                proc.terminate()
                out, _ = proc.communicate(timeout=20)
                self.assertEqual(proc.returncode, 0, out)
                self.assertFalse(Path(sock_path).exists(), "socket left behind")
                self.assertFalse(Path(tmp, "cap").exists(), "capability file left behind")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()

    def test_broker_refuses_everything_after_it_is_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sock_path = f"{tmp}/b.sock"
            ready = f"{tmp}/ready"
            key = Path(tmp) / "key"
            key.write_text("sk-fake-test-key\n")
            key.chmod(0o600)
            proc = subprocess.Popen(
                [
                    sys.executable, "-m", "code_shipper.inference.runner",
                    "--socket", sock_path,
                    "--ledger", f"{tmp}/r.jsonl",
                    "--provider", "fake",
                    "--model", "fake-model",
                    "--upstream-url", "http://127.0.0.1:1",
                    "--credential-file", str(key),
                    "--capability-out", f"{tmp}/cap",
                    "--ready-file", ready,
                ],
                cwd=REPO,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "PYTHONPATH": str(REPO)},
            )
            try:
                deadline = time.time() + 20
                while not Path(ready).exists() and time.time() < deadline:
                    time.sleep(0.05)
                proc.terminate()
                proc.communicate(timeout=20)

                with self.assertRaises(OSError):
                    one_request(sock_path, "any-capability")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777


def one_request(sock_path: str, capability: str) -> dict:
    payload = {
        "op": "complete",
        "capability": capability,
        "provider": "fake",
        "model": "fake-model",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 8,
    }
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(10)
    try:
        sock.connect(sock_path)
        sock.sendall(json.dumps(payload).encode() + b"\n")
        raw = b""
        while b"\n" not in raw:
            chunk = sock.recv(4096)
            if not chunk:
                break
            raw += chunk
    finally:
        sock.close()
    return json.loads(raw.split(b"\n", 1)[0])


if __name__ == "__main__":
    unittest.main()
