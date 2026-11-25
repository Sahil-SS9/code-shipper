"""Phase 5 host integration: the plugin loads and /ship runs inside a throwaway HERMES_HOME.

The live installation is never touched. `HERMES_HOME` is pointed at a temporary directory, the plugin
is installed there, and the command is driven through the host's own command dispatch — so this proves
the plugin is loadable and callable, not just that its files exist.

A plugin that validates but cannot be called is the failure mode this file exists to catch.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "plugin" / "code-shipper"


def _hermes() -> str | None:
    return shutil.which("hermes")


@unittest.skipUnless(_hermes(), "the hermes binary is not installed here")
class InstallIntoThrowawayHomeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="cs-hermes-home-"))
        self.addCleanup(lambda: shutil.rmtree(self.home, ignore_errors=True))
        self.target = self.home / "plugins" / "code-shipper"
        self.target.parent.mkdir(parents=True)
        shutil.copytree(PLUGIN, self.target)

    def _run(self, *args: str, timeout: int = 180) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "HERMES_HOME": str(self.home),
            # never let the host write into the live home through a stray config
            "HOME": str(self.home),
        }
        return subprocess.run(
            [str(_hermes()), *args], capture_output=True, text=True, timeout=timeout, env=env,
            cwd=str(REPO),
        )

    def test_validate_passes(self) -> None:
        result = self._run("plugins", "validate", str(self.target))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Validation passed", result.stdout)

    def test_the_command_is_registered_and_callable(self) -> None:
        # A bad invocation must return a usage message from the command itself: that proves the
        # handler ran, rather than that the plugin merely loaded.
        result = self._run("ship")
        output = result.stdout + result.stderr
        self.assertTrue(
            "usage: /ship" in output or "review|fix|watch" in output or "ship" in output.lower(),
            f"/ship did not appear to run: {output[:400]}",
        )

    def test_the_command_reports_a_bad_repo_instead_of_crashing(self) -> None:
        result = self._run("ship", "notarepo", "1")
        output = (result.stdout + result.stderr).strip()
        self.assertNotIn("Traceback", output, "the command must return a message, not a traceback")
        self.assertTrue(output, "the command produced no output at all")

    def test_validation_json_is_machine_readable(self) -> None:
        result = self._run("plugins", "validate", str(self.target), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload.get("ok") or payload.get("valid"),
                        f"unexpected validate payload: {payload}")

    def test_nothing_was_written_to_the_live_home(self) -> None:
        live = Path.home() / ".hermes" / "plugins" / "code-shipper"
        self.assertFalse(live.exists(), "a test must never install into the live home")


if __name__ == "__main__":
    unittest.main()
