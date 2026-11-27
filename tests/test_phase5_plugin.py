"""Phase 5 plugin tests: the command wrapper, and installing into a throwaway HERMES_HOME.

The wrapper is tested without a host: `build_argv` proves the two surfaces cannot drift (each
subcommand maps to a real CLI verb), and `run` proves a bad invocation returns a message rather than
raising into the host.

The install test uses a temporary directory as HERMES_HOME. It never touches ~/.hermes — the live
installation is not under test and must not be modified by a test.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / "plugin" / "code-shipper"


def _load_command():
    spec = importlib.util.spec_from_file_location(
        "cs_plugin_ship", PLUGIN / "commands" / "ship.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ParseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ship = _load_command()

    def test_a_repo_and_pr_are_parsed(self) -> None:
        self.assertEqual(self.ship.parse("o/r 5"), ("o/r", 5, "review"))

    def test_a_hash_prefix_is_accepted(self) -> None:
        self.assertEqual(self.ship.parse("o/r #7 review"), ("o/r", 7, "review"))

    def test_empty_arguments_show_usage(self) -> None:
        with self.assertRaises(self.ship.Refused) as caught:
            self.ship.parse("")
        self.assertIn("usage", caught.exception.message)

    def test_a_missing_repo_slash_is_refused(self) -> None:
        with self.assertRaises(self.ship.Refused):
            self.ship.parse("justarepo 5")

    def test_a_non_numeric_pr_is_refused(self) -> None:
        with self.assertRaises(self.ship.Refused):
            self.ship.parse("o/r notanumber")

    def test_an_unknown_subcommand_is_refused(self) -> None:
        with self.assertRaises(self.ship.Refused) as caught:
            self.ship.parse("o/r 1 deploy")
        self.assertIn("unknown subcommand", caught.exception.message)


class ArgvTests(unittest.TestCase):
    """Each subcommand must map to a verb the CLI actually has; drift here breaks the surface."""

    def setUp(self) -> None:
        self.ship = _load_command()

    def _cli_verbs(self) -> set[str]:
        repo = PLUGIN.parent.parent
        result = subprocess.run(
            [sys.executable, "-m", "code_shipper.cli", "--help"],
            cwd=str(repo), capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = result.stdout
        start = text.find("{")
        end = text.find("}", start)
        return set(text[start + 1:end].split(",")) if start != -1 else set()

    def test_every_mapped_verb_exists_in_the_cli(self) -> None:
        verbs = self._cli_verbs()
        self.assertTrue(verbs, "could not read the CLI's subcommands")
        for subcommand in self.ship.SUBCOMMANDS:
            argv = self.ship.build_argv("o/r", 2, subcommand)
            self.assertIn(argv[1], verbs,
                          f"/ship {subcommand} maps to {argv[1]!r}, which the CLI does not have")

    def test_review_passes_the_profile(self) -> None:
        argv = self.ship.build_argv("o/r", 2, "review", profile="assertive")
        self.assertIn("--profile", argv)
        self.assertIn("assertive", argv)

    def test_every_invocation_parses_with_the_real_cli(self) -> None:
        # Checking the verb alone let `/ship stop` drop the PR number and `/ship status` drop its
        # action: both reached the CLI and died on a usage error.
        from code_shipper.cli import build_parser

        parser = build_parser()
        for subcommand in self.ship.SUBCOMMANDS:
            argv = self.ship.build_argv("o/r", 2, subcommand)
            with self.subTest(subcommand=subcommand):
                try:
                    args = parser.parse_args(argv[1:])
                except SystemExit:
                    self.fail(f"/ship {subcommand} builds {argv[1:]}, which the CLI rejects")
                if hasattr(args, "pr"):
                    self.assertEqual(args.pr, 2)
                self.assertEqual(args.repo, "o/r")

    def test_polling_is_not_a_chat_command(self) -> None:
        # A chat command waits for its child; a poll loop would hold the agent until the timeout.
        self.assertNotIn("poll", self.ship.SUBCOMMANDS.values())
        self.assertNotIn("watch", self.ship.SUBCOMMANDS)

    def test_publish_is_refused_without_a_branch(self) -> None:
        with self.assertRaises(self.ship.Refused) as caught:
            self.ship.build_argv("o/r", 2, "publish")
        self.assertIn("--branch", caught.exception.message)


class RunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ship = _load_command()

    class _Completed:
        def __init__(self, code=0, out="", err=""):
            self.returncode = code
            self.stdout = out
            self.stderr = err

    def test_a_bad_invocation_returns_instead_of_raising(self) -> None:
        result = self.ship.run("", runner=lambda argv: self._Completed())
        self.assertFalse(result["ok"])
        self.assertIn("usage", result["output"])

    def test_publish_is_refused_when_allow_ship_is_off(self) -> None:
        result = self.ship.run("o/r 1 publish", allow_ship=False,
                               runner=lambda argv: self._Completed())
        self.assertFalse(result["ok"])
        self.assertIn("disabled", result["output"])

    def test_a_missing_binary_is_a_message(self) -> None:
        def boom(argv):
            raise FileNotFoundError(argv[0])

        result = self.ship.run("o/r 1 review", runner=boom)
        self.assertFalse(result["ok"])
        self.assertIn("was not found", result["output"])

    def test_successful_output_is_returned(self) -> None:
        result = self.ship.run(
            "o/r 1 review", runner=lambda argv: self._Completed(0, "one finding\n")
        )
        self.assertTrue(result["ok"])
        self.assertIn("one finding", result["output"])

    def test_a_failing_command_is_reported_as_a_failure(self) -> None:
        result = self.ship.run(
            "o/r 1 review", runner=lambda argv: self._Completed(2, "", "refused: nope")
        )
        self.assertFalse(result["ok"])
        self.assertIn("refused: nope", result["output"])

    def test_the_token_is_never_put_on_the_command_line(self) -> None:
        # a token in argv appears in a process listing to every user on the machine
        result = self.ship.run("o/r 1 review", runner=lambda argv: self._Completed())
        for argument in result["argv"]:
            self.assertNotIn("ghp_", argument)
            self.assertNotIn("--token", argument)


class ChildEnvTests(unittest.TestCase):
    """The CLI gets an allowlisted environment, never the agent's own."""

    def setUp(self) -> None:
        self.ship = _load_command()

    def test_keys_tokens_and_run_settings_are_not_passed(self) -> None:
        env = self.ship.child_env({
            "PATH": "/usr/bin", "HOME": "/home/alice", "HERMES_HOME": "/home/alice/.hermes",
            "OPENAI_API_KEY": "sk-x", "ANTHROPIC_API_KEY": "sk-y", "GITHUB_TOKEN": "ghp_x",
            "CODE_SHIPPER_GITHUB_TOKEN": "ghp_y", "CODE_SHIPPER_API_KEY": "k",
            "HERMES_YOLO_MODE": "1", "HERMES_INTERACTIVE": "0", "AWS_SECRET_ACCESS_KEY": "a",
        })
        self.assertEqual(env, {"PATH": "/usr/bin", "HOME": "/home/alice",
                               "HERMES_HOME": "/home/alice/.hermes"})

    def test_the_real_runner_passes_the_allowlisted_env(self) -> None:
        seen = {}

        def fake_run(argv, **kwargs):
            seen.update(kwargs)
            return RunTests._Completed()

        original = self.ship.subprocess.run
        self.ship.subprocess.run = fake_run
        try:
            with unittest.mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-x", "PATH": "/usr/bin"}):
                self.ship._subprocess_runner(["code-shipper", "review"])
        finally:
            self.ship.subprocess.run = original
        self.assertIn("env", seen)
        self.assertNotIn("OPENAI_API_KEY", seen["env"])
        self.assertEqual(seen["env"].get("PATH"), "/usr/bin")
        self.assertIs(seen["stdin"], subprocess.DEVNULL)


class InstallTests(unittest.TestCase):
    """A throwaway HERMES_HOME only. ~/.hermes is never touched."""

    def test_the_manifest_is_valid(self) -> None:
        text = (PLUGIN / "plugin.yaml").read_text()
        self.assertIn("name: code-shipper", text)
        self.assertIn("manifest_version: 2", text)
        self.assertIn("commands:", text)
        self.assertIn("/ship", text)

    def test_the_manifest_declares_no_token_setting(self) -> None:
        # The plugin never reads a credential; a token-file setting would suggest it does.
        self.assertNotIn("token", (PLUGIN / "plugin.yaml").read_text().lower())

    def test_the_manifest_and_module_versions_agree(self) -> None:
        text = (PLUGIN / "plugin.yaml").read_text()
        manifest = next(l.split(":", 1)[1].strip() for l in text.splitlines() if l.startswith("version:"))
        module = (PLUGIN / "__init__.py").read_text()
        self.assertIn(f'__version__ = "{manifest}"', module)

    def test_allow_ship_defaults_off(self) -> None:
        text = (PLUGIN / "plugin.yaml").read_text()
        line = next(line for line in text.splitlines() if "allow_ship" in line)
        self.assertIn("default: false", line,
                      "a publish capability must not be on by default")

    def test_install_into_a_throwaway_home(self) -> None:
        import tempfile
        import shutil

        home = Path(tempfile.mkdtemp(prefix="cs-hermes-home-"))
        self.addCleanup(lambda: shutil.rmtree(home, ignore_errors=True))
        target = home / "plugins" / "code-shipper"
        target.parent.mkdir(parents=True)
        shutil.copytree(PLUGIN, target)

        self.assertTrue((target / "plugin.yaml").is_file())
        self.assertTrue((target / "commands" / "ship.py").is_file())

        # The command imports and parses inside the throwaway home, with no host present.
        spec = importlib.util.spec_from_file_location(
            "installed_ship", target / "commands" / "ship.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.parse("o/r 3"), ("o/r", 3, "review"))
        self.assertIn("never merges", module.USAGE)

    def test_the_live_home_is_not_touched(self) -> None:
        # A guard on the test itself: if this ever runs against the live home, it is a bug.
        self.assertNotIn(str(Path.home() / ".hermes" / "plugins" / "code-shipper"),
                         str(PLUGIN.parent))


if __name__ == "__main__":
    unittest.main()
