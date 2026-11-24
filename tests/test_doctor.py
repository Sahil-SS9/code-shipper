"""Doctor tests: the three-way status must fail in the safe direction.

The rule that matters: `allow_agent_approval` requires a VERIFIED container backend, so anything that
cannot be proven must report UNVERIFIED or UNSUPPORTED. A backend that reports VERIFIED on the
strength of being installed would turn a safety setting on when it should stay off.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from code_shipper import doctor
from code_shipper.doctor import (
    UNSUPPORTED,
    UNVERIFIED,
    VERIFIED,
    Check,
    Report,
    check_harness,
    env_audit,
)

REPO = Path(__file__).resolve().parent.parent


class OverallTests(unittest.TestCase):
    def test_all_verified_is_verified(self) -> None:
        report = Report()
        self.assertEqual(report.overall([Check("a", VERIFIED), Check("b", VERIFIED)]), VERIFIED)

    def test_one_unverified_downgrades_the_backend(self) -> None:
        report = Report()
        self.assertEqual(report.overall([Check("a", VERIFIED), Check("b", UNVERIFIED)]), UNVERIFIED)

    def test_one_unsupported_makes_it_unusable(self) -> None:
        report = Report()
        self.assertEqual(
            report.overall([Check("a", VERIFIED), Check("b", UNSUPPORTED)]), UNSUPPORTED
        )

    def test_no_checks_is_not_verified(self) -> None:
        # absence of evidence must never read as success
        self.assertEqual(Report().overall([]), UNSUPPORTED)


class ContainerVerifiedTests(unittest.TestCase):
    def test_a_verified_docker_means_container_verified(self) -> None:
        report = Report(backends={"docker-rootless": [Check("a", VERIFIED)]})
        self.assertTrue(report.container_verified())

    def test_an_unverified_docker_does_not(self) -> None:
        report = Report(backends={"docker-rootless": [Check("a", UNVERIFIED)]})
        self.assertFalse(report.container_verified())

    def test_an_unverified_bubblewrap_does_not(self) -> None:
        report = Report(backends={"bubblewrap": [Check("bwrap", UNVERIFIED, "present, unmeasured")]})
        self.assertFalse(report.container_verified())

    def test_no_container_backend_at_all_does_not(self) -> None:
        self.assertFalse(Report().container_verified())


class RenderTests(unittest.TestCase):
    def test_render_names_every_backend_and_status(self) -> None:
        report = Report(
            backends={"docker-rootless": [Check("context", VERIFIED, "rootless")]},
            harnesses={"hermes": [Check("hermes CLI", VERIFIED, "v1")]},
        )
        text = report.render()
        self.assertIn("docker-rootless: VERIFIED", text)
        self.assertIn("hermes: VERIFIED", text)
        self.assertIn("rootless", text)

    def test_json_output_is_serialisable(self) -> None:
        report = Report(backends={"x": [Check("a", UNVERIFIED, "d")]})
        decoded = json.loads(json.dumps(report.as_dict()))
        self.assertIn("x", decoded["backends"])
        self.assertEqual(decoded["backends"]["x"]["status"], UNVERIFIED)


class HarnessStatusTests(unittest.TestCase):
    def test_an_unverified_preset_is_not_reported_verified(self) -> None:
        # claude is installed here but its preset has never passed a contract test
        checks = check_harness("command")
        claude = next((c for c in checks if c.name == "claude"), None)
        if claude is not None and claude.status != UNSUPPORTED:
            self.assertEqual(claude.status, UNVERIFIED)

    def test_a_failed_contract_probe_downgrades_hermes(self) -> None:
        # present but not working must not read as VERIFIED
        with mock.patch("shutil.which", return_value="/usr/bin/hermes"), \
             mock.patch("subprocess.run") as run, \
             mock.patch("code_shipper.harness.contract_probe.probe",
                        return_value={"ok": False, "reason": "provider never called"}):
            run.return_value = mock.Mock(returncode=0, stdout="Hermes Agent v9", stderr="")
            checks = check_harness("hermes")
        contract = next(c for c in checks if c.name == "contract one-shot")
        self.assertEqual(contract.status, UNVERIFIED)
        self.assertIn("never called", contract.detail)

    def test_a_passing_contract_probe_reports_verified(self) -> None:
        with mock.patch("shutil.which", return_value="/usr/bin/hermes"), \
             mock.patch("subprocess.run") as run, \
             mock.patch("code_shipper.harness.contract_probe.probe",
                        return_value={"ok": True, "reason": "one-shot completed"}):
            run.return_value = mock.Mock(returncode=0, stdout="Hermes Agent v9", stderr="")
            checks = check_harness("hermes")
        self.assertEqual(Report().overall(checks), VERIFIED)

    def test_a_probe_that_raises_does_not_crash_doctor(self) -> None:
        with mock.patch("shutil.which", return_value="/usr/bin/hermes"), \
             mock.patch("subprocess.run") as run, \
             mock.patch("code_shipper.harness.contract_probe.probe",
                        side_effect=RuntimeError("no temp dir")):
            run.return_value = mock.Mock(returncode=0, stdout="Hermes Agent v9", stderr="")
            checks = check_harness("hermes")
        contract = next(c for c in checks if c.name == "contract one-shot")
        self.assertEqual(contract.status, UNVERIFIED)

    def test_a_missing_hermes_is_unsupported_without_running_a_probe(self) -> None:
        with mock.patch("shutil.which", return_value=None), \
             mock.patch("code_shipper.harness.contract_probe.probe") as probe_mock:
            checks = check_harness("hermes")
        self.assertEqual(Report().overall(checks), UNSUPPORTED)
        probe_mock.assert_not_called()

    def test_missing_presets_are_unsupported(self) -> None:
        checks = check_harness("command")
        for check in checks:
            if "not found" in check.detail:
                self.assertEqual(check.status, UNSUPPORTED)

    def test_an_unknown_harness_is_unsupported(self) -> None:
        self.assertEqual(check_harness("nope")[0].status, UNSUPPORTED)

    def test_the_direct_api_without_a_key_is_unsupported(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(check_harness("openai_compat")[0].status, UNSUPPORTED)


class EnvAuditTests(unittest.TestCase):
    """T-ADV-07."""

    def test_a_clean_environment_passes(self) -> None:
        checks = env_audit({"PATH": "/usr/bin", "HOME": "/tmp/nonexistent-home"}, home="/tmp/nonexistent-home")
        env_check = next(c for c in checks if c.name == "environment")
        self.assertEqual(env_check.status, VERIFIED)

    def test_a_github_token_in_the_environment_fails(self) -> None:
        checks = env_audit(
            {"PATH": "/usr/bin", "GH_TOKEN": "x", "HOME": "/tmp/nonexistent-home"},
            home="/tmp/nonexistent-home",
        )
        env_check = next(c for c in checks if c.name == "environment")
        self.assertEqual(env_check.status, UNSUPPORTED)
        self.assertIn("GH_TOKEN", env_check.detail)

    def test_a_present_credential_file_fails(self) -> None:
        home = Path(tempfile.mkdtemp(prefix="cs-home-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(home, ignore_errors=True))
        (home / ".git-credentials").write_text("https://user:token@github.com\n")
        checks = env_audit({"PATH": "/usr/bin", "HOME": str(home)}, home=str(home))
        cred = next(c for c in checks if c.name == "git credentials file")
        self.assertEqual(cred.status, UNSUPPORTED)

    def test_the_audit_never_opens_the_credential_file(self) -> None:
        # the detail must name the path, never its contents
        home = Path(tempfile.mkdtemp(prefix="cs-home2-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(home, ignore_errors=True))
        secret = "ghp_shouldneverappear"
        (home / ".git-credentials").write_text(f"https://user:{secret}@github.com\n")
        checks = env_audit({"PATH": "/usr/bin", "HOME": str(home)}, home=str(home))
        rendered = " ".join(c.detail for c in checks)
        self.assertNotIn(secret, rendered)


class DiagnoseTests(unittest.TestCase):
    def test_diagnose_returns_a_report_and_never_raises(self) -> None:
        report = doctor.diagnose(env={"PATH": "/usr/bin"}, contract=False)
        self.assertIsInstance(report, Report)
        self.assertIn("docker-rootless", report.backends)

    def test_it_does_not_claim_docker_when_docker_is_absent(self) -> None:
        with mock.patch("shutil.which", return_value=None):
            report = doctor.diagnose(env={"PATH": "/usr/bin"}, contract=False)
        self.assertEqual(report.backend_status("docker-rootless"), UNSUPPORTED)


class CliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-B", "-m", "code_shipper.cli", *args],
            cwd=REPO, capture_output=True, text=True, timeout=120,
        )

    def test_doctor_prints_a_report(self) -> None:
        proc = self.run_cli("doctor", "--no-contract")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("container backends:", proc.stdout)

    def test_doctor_json_is_valid(self) -> None:
        proc = self.run_cli("doctor", "--json", "--no-contract")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("backends", json.loads(proc.stdout))

    def test_require_refuses_an_unverified_backend(self) -> None:
        proc = self.run_cli("doctor", "--require", "bubblewrap", "--no-contract")
        if "bubblewrap: VERIFIED" in proc.stdout:
            self.assertEqual(proc.returncode, 0)
        else:
            self.assertEqual(proc.returncode, 2)
            self.assertIn("not VERIFIED", proc.stderr)

    def test_require_unknown_backend_fails(self) -> None:
        self.assertEqual(self.run_cli("doctor", "--require", "imaginary", "--no-contract").returncode, 2)

    def test_version_works(self) -> None:
        proc = self.run_cli("--version")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("code-shipper", proc.stdout)


if __name__ == "__main__":
    unittest.main()
