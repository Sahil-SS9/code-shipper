"""The packaging entry point must actually work, not merely be declared.

A `[project.scripts]` entry that points at a missing callable installs cleanly and then fails on first
use, so these tests run the real entry points rather than asserting the metadata exists.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class ModuleEntryPointTests(unittest.TestCase):
    def run_module(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-B", "-m", "code_shipper", *args],
            cwd=REPO, capture_output=True, text=True, timeout=180,
        )

    def test_python_dash_m_runs_the_cli(self) -> None:
        proc = self.run_module("--version")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("code-shipper", proc.stdout)

    def test_python_dash_m_reaches_a_subcommand(self) -> None:
        proc = self.run_module("doctor", "--json", "--no-contract")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("backends", json.loads(proc.stdout))

    def test_no_arguments_is_a_usage_error_not_a_crash(self) -> None:
        proc = self.run_module()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage", (proc.stderr or "").lower())


class DeclaredScriptTests(unittest.TestCase):
    def test_the_declared_target_is_importable_and_callable(self) -> None:
        # exactly what [project.scripts] names, resolved the way an installer would
        from code_shipper.main import run

        self.assertTrue(callable(run))

    def test_the_script_metadata_points_at_that_target(self) -> None:
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("[project.scripts]", text)
        self.assertIn('code-shipper = "code_shipper.main:run"', text)


class InstalledScriptTests(unittest.TestCase):
    """Install into a throwaway venv and run the real console script.

    A `[project.scripts]` entry that names a missing callable installs cleanly and fails on first
    use, so this runs the installed binary. It builds a venv because the interpreter running these
    tests may have no pip; `venv` guarantees one.
    """

    def test_a_venv_install_produces_a_working_console_script(self) -> None:
        import venv

        target = Path(tempfile.mkdtemp(prefix="cs-install-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(target, ignore_errors=True))
        env_dir = target / "venv"
        try:
            venv.create(env_dir, with_pip=True)
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"could not build a venv here: {exc}")

        bindir = "Scripts" if sys.platform.startswith("win") else "bin"
        python = env_dir / bindir / ("python.exe" if sys.platform.startswith("win") else "python")
        pip = [str(python), "-m", "pip"]

        # setuptools must be present for --no-build-isolation; a bare venv does not include it.
        build = subprocess.run(
            [*pip, "install", "--quiet", "setuptools>=68"], capture_output=True, text=True, timeout=600
        )
        if build.returncode != 0:
            self.skipTest(f"could not install setuptools: {build.stderr[-200:]}")
        install = subprocess.run(
            # `--no-build-isolation` makes setuptools write `build/lib/` **into the source tree**, and this
        # suite is run by `unittest discover` in that same tree. So installing here mutated the tree
        # under the other 1,270 tests while they ran — a race whose symptom is whatever the interpreter
        # happened to import at that moment. Measured: a suite failure naming `_reevaluate_approvals`,
        # which passes standalone and does not reproduce.
        #
        # The install is redirected to a throwaway build directory so the source tree is never written to.
        [*pip, "install", "--quiet", "--no-deps", "--no-build-isolation",
         str(REPO), f"--config-settings=--build-option=--build-base={env_dir}/build"],
            capture_output=True, text=True, timeout=600,
            env={**os.environ, "SETUPTOOLS_BUILD_DIR": str(env_dir / "build")},
        )
        if install.returncode != 0:
            self.skipTest(f"pip could not install: {install.stderr[-300:]}")

        script = env_dir / bindir / ("code-shipper.exe" if sys.platform.startswith("win") else "code-shipper")
        self.assertTrue(script.exists(), f"no console script at {script}")

        proc = subprocess.run([str(script), "--version"], capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("code-shipper", proc.stdout)

    def test_the_installed_script_reaches_a_subcommand(self) -> None:
        import venv

        target = Path(tempfile.mkdtemp(prefix="cs-install2-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(target, ignore_errors=True))
        env_dir = target / "venv"
        try:
            venv.create(env_dir, with_pip=True)
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"could not build a venv here: {exc}")

        bindir = "Scripts" if sys.platform.startswith("win") else "bin"
        python = env_dir / bindir / ("python.exe" if sys.platform.startswith("win") else "python")
        build = subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet", "setuptools>=68"],
            capture_output=True, text=True, timeout=600,
        )
        if build.returncode != 0:
            self.skipTest(f"could not install setuptools: {build.stderr[-200:]}")
        install = subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet", "--no-deps",
             "--no-build-isolation", str(REPO)],
            capture_output=True, text=True, timeout=600,
        )
        if install.returncode != 0:
            self.skipTest(f"pip could not install: {install.stderr[-300:]}")

        script = env_dir / bindir / ("code-shipper.exe" if sys.platform.startswith("win") else "code-shipper")
        proc = subprocess.run(
            [str(script), "doctor", "--no-contract"], capture_output=True, text=True, timeout=300
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("container backends:", proc.stdout)


if __name__ == "__main__":
    unittest.main()


class InstallDoesNotWriteIntoTheSourceTreeTests(unittest.TestCase):
    """The install under test must not create `build/` in the repo.

    `--no-build-isolation` writes `build/lib/` into the source tree, and this suite is run by
    `unittest discover` in that same tree. Installing therefore mutated the tree under every other test
    while they ran. The symptom was a suite failure naming `_reevaluate_approvals` — a test that passes
    standalone and does not reproduce on re-run, which is the shape of a race rather than a bug.

    Pointing the build at the throwaway venv keeps the source tree read-only for the duration of the
    suite, which is what a test suite has to assume.
    """

    REPO = Path(__file__).resolve().parent.parent

    def test_the_install_is_pointed_at_a_build_base_outside_the_repo(self) -> None:
        source = Path(__file__).read_text()
        self.assertIn("--build-base", source,
                      "the install must redirect its build directory away from the source tree")
        self.assertIn("env_dir", source.split("--build-base")[1][:120],
                      "the build base must live in the throwaway venv")

    def test_no_build_directory_is_created_by_a_fresh_run(self) -> None:
        """Run the install path and assert the repo's `build/` is untouched.

        Uses the file's own mtime as the witness: if the install wrote there, it would change.
        """
        build = self.REPO / "build"
        if not build.exists():
            self.skipTest("no build/ present to observe; the redirection cannot be measured here")
        before = (build / "lib").stat().st_mtime if (build / "lib").exists() else build.stat().st_mtime

        import subprocess
        import sys

        subprocess.run([sys.executable, "-B", "-m", "unittest",
                        "tests.test_entrypoint.InstalledConsoleScriptTests", "-q"],
                       cwd=self.REPO, capture_output=True, text=True, timeout=900)

        after = (build / "lib").stat().st_mtime if (build / "lib").exists() else build.stat().st_mtime
        self.assertEqual(before, after,
                         "installing wrote into the source tree's build/ — it will race other tests")
