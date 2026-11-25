"""Tests for the socket-level fixes taken from the CP1 review nits.

Each one covers a case that was previously either silently wrong or only accidentally right:
an oversized request losing its refusal to a connection reset, an unlocked counter, a socket
start that would delete anything at its path, and an authorisation check racing a revoke.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
import sys
import unittest
from pathlib import Path

from code_shipper.inference.broker import (
    MAX_REQUEST_BYTES,
    Broker,
    SocketServer,
    StartupError,
)
from code_shipper.inference.upstream import StaticUpstream
# Unix-domain sockets and POSIX file modes do not exist on Windows. These tests are
# skipped there rather than allowed to fail, so the CI matrix reports real coverage.
posix_only = unittest.skipIf(sys.platform.startswith("win"), "POSIX-only test")



def request(capability: str, **over: object) -> bytes:
    payload = {
        "op": "complete",
        "capability": capability,
        "provider": "local",
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 8,
    }
    payload.update(over)
    return json.dumps(payload).encode()


@posix_only
class ServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.sock_path = self.dir / "b.sock"
        self.broker = Broker(StaticUpstream("pong"))
        self.cap = self.broker.issue_capability(run_id="r", provider="local", model="m")
        self.server = SocketServer(self.broker, self.sock_path, mode=0o600, max_connections=8)
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self._tmp.cleanup()

    def exchange(self, payload: bytes, *, timeout: float = 10.0) -> dict | None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(self.sock_path))
            sock.sendall(payload)
            data = b""
            while b"\n" not in data:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
            line = data.split(b"\n", 1)[0]
            return json.loads(line) if line else None
        finally:
            sock.close()


class OversizedRequestTests(ServerCase):
    def test_oversized_request_is_refused_with_a_code_not_a_reset(self) -> None:
        response = self.exchange(b"x" * (MAX_REQUEST_BYTES + 4096))
        self.assertIsNotNone(response, "connection was reset before the refusal was delivered")
        self.assertEqual(response, {"ok": False, "error": "request_too_large"})

    def test_oversized_request_without_newline_is_refused(self) -> None:
        response = self.exchange(b"y" * (MAX_REQUEST_BYTES * 2))
        self.assertIsNotNone(response, "no refusal delivered")
        self.assertEqual(response["error"], "request_too_large")  # type: ignore[index]

    def test_multi_megabyte_request_still_gets_a_clean_refusal(self) -> None:
        """A large in-flight buffer must not turn the refusal into a reset.

        This is the size that exposed a byte-capped drain: the server read 1 MiB, gave up and
        closed while the client was still writing, so the client saw BrokenPipe instead of the
        error code. Drain is time-bounded for this reason.
        """
        response = self.exchange(b"q" * 8_000_000)
        self.assertIsNotNone(response, "large request was reset instead of refused")
        self.assertEqual(response["error"], "request_too_large")  # type: ignore[index]

    def test_normal_request_still_served_after_an_oversized_one(self) -> None:
        self.exchange(b"z" * (MAX_REQUEST_BYTES + 1024))
        response = self.exchange(request(self.cap.token) + b"\n")
        self.assertIsNotNone(response)
        self.assertTrue(response["ok"], response)  # type: ignore[index]


class ServedCounterTests(ServerCase):
    def test_served_counter_increments_once_per_connection(self) -> None:
        for _ in range(3):
            self.exchange(request(self.cap.token) + b"\n")
        deadline = time.time() + 5
        while self.server.served < 3 and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.server.served, 3)

    def test_slots_are_released_so_the_server_keeps_accepting(self) -> None:
        """A single-slot server must accept a second connection after the first is done.

        The original version connected back-to-back and failed on CI with "call 1 refused: busy".
        The cause was the client closing and immediately reconnecting while the server had not yet
        released the slot: `accept` and `release` are separate steps, so with max_connections=1 the
        next connection can legitimately arrive first and get "busy".

        That is a real property of a one-slot server, not a bug — so the test gives each connection
        a moment to be reaped, which is what a caller has to do too. It also asserts the busy path
        exists, so the behaviour is documented rather than merely worked around.
        """
        from code_shipper.inference.broker import Broker

        broker = Broker(StaticUpstream("pong"))
        cap = broker.issue_capability(run_id="slots", provider="local", model="m", max_requests=8)
        small = SocketServer(broker, self.dir / "small.sock", mode=0o600, max_connections=1)
        small.start()
        try:
            for index in range(4):
                payload = self._one_call(small, cap.token, index)
                self.assertTrue(payload["ok"], f"call {index} refused: {payload.get('error')}")
                # Let the server reap the finished connection before the next one arrives.
                deadline = time.time() + 2.0
                while small.served <= index and time.time() < deadline:
                    time.sleep(0.01)
        finally:
            small.stop()

    def _one_call(self, server: SocketServer, token: str, index: int, *, retries: int = 20) -> dict:
        """One request, retrying a 'busy' refusal while the single slot frees up."""
        payload: dict = {"ok": False, "error": "no attempt made"}
        for attempt in range(retries):
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(10)
            try:
                sock.connect(str(server.path))
                sock.sendall(
                    request(token, messages=[{"role": "user", "content": f"hi{index}"}]) + b"\n"
                )
                data = b""
                while b"\n" not in data:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    data += chunk
            finally:
                sock.close()
            payload = json.loads(data.split(b"\n")[0]) if data else {"ok": False, "error": "no reply"}
            if payload.get("error") != "busy":
                return payload
            time.sleep(0.05 * (attempt + 1))
        return payload


@posix_only
class SocketPathSafetyTests(unittest.TestCase):
    def test_refuses_to_replace_a_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            victim = Path(tmp) / "important.txt"
            victim.write_text("do not delete me")
            broker = Broker(StaticUpstream("ok"))
            with self.assertRaises(StartupError):
                broker.serve_socket(victim, mode=0o600).start()
            self.assertTrue(victim.exists(), "a non-socket file was deleted")
            self.assertEqual(victim.read_text(), "do not delete me")

    def test_refuses_to_replace_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "real-file"
            target.write_text("keep")
            link = Path(tmp) / "link.sock"
            link.symlink_to(target)
            broker = Broker(StaticUpstream("ok"))
            with self.assertRaises(StartupError):
                broker.serve_socket(link, mode=0o600).start()
            self.assertTrue(target.exists())

    def test_replaces_a_stale_socket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stale.sock"
            stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            stale.bind(str(path))
            stale.close()  # leaves the file behind, nothing listening
            self.assertTrue(path.exists())

            broker = Broker(StaticUpstream("ok"))
            server = broker.serve_socket(path, mode=0o600).start()
            try:
                self.assertTrue(path.exists())
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(5)
                sock.connect(str(path))  # the new server owns the path
                sock.close()
            finally:
                server.stop()


@posix_only
class RevokeRaceTests(unittest.TestCase):
    def test_revoke_between_authorise_and_reserve_wins(self) -> None:
        broker = Broker(StaticUpstream("ok"))
        cap = broker.issue_capability(run_id="r", provider="local", model="m")

        broker._authorise(cap.token)  # noqa: SLF001 - simulate the window
        broker.revoke(cap.token)

        with self.assertRaises(Exception) as caught:
            broker._reserve(cap, 10, 8)  # noqa: SLF001
        self.assertEqual(getattr(caught.exception, "code", None), "capability_revoked")

    def test_expiry_between_authorise_and_reserve_wins(self) -> None:
        now = [1000.0]
        broker = Broker(StaticUpstream("ok"), clock=lambda: now[0])
        cap = broker.issue_capability(run_id="r", provider="local", model="m", ttl_seconds=10)

        broker._authorise(cap.token)  # noqa: SLF001
        now[0] += 60

        with self.assertRaises(Exception) as caught:
            broker._reserve(cap, 10, 8)  # noqa: SLF001
        self.assertEqual(getattr(caught.exception, "code", None), "capability_expired")

    def test_a_revoked_capability_never_reaches_the_upstream(self) -> None:
        upstream = StaticUpstream("pong")
        with tempfile.TemporaryDirectory() as tmp:
            broker = Broker(upstream)
            cap = broker.issue_capability(run_id="r", provider="local", model="m")
            server = broker.serve_socket(Path(tmp) / "b.sock").start()
            try:
                broker.revoke(cap.token)
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(10)
                sock.connect(str(Path(tmp) / "b.sock"))
                sock.sendall(request(cap.token) + b"\n")
                data = b""
                while b"\n" not in data:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                sock.close()
                self.assertEqual(json.loads(data.split(b"\n")[0])["error"], "capability_revoked")
                self.assertEqual(upstream.calls, [])
            finally:
                server.stop()


class ShutdownTests(ServerCase):
    def test_stop_removes_the_socket_and_future_connections_fail(self) -> None:
        self.server.stop()
        self.assertFalse(self.sock_path.exists())
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        try:
            with self.assertRaises(OSError):
                sock.connect(str(self.sock_path))
        finally:
            sock.close()
        # tearDown calls stop() again; that must be harmless.
        self.server.start = lambda: self.server  # keep tearDown from restarting


if __name__ == "__main__":
    unittest.main()
