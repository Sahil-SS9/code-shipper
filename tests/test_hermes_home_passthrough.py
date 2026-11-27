"""A review runs on the Hermes install the user selected, or it says it did not.

Kensei's fresh-install finding: `HERMES_HOME` never survived `scrubbed_env`, so a "throwaway" review
silently fell back to the default profile and spent real money on the owner's main Claude account. The
selection keys are not secrets — the credential path is, and that stays out.
"""

from __future__ import annotations

import unittest

from code_shipper.harness.adapters import HERMES_SELECTION_KEYS, _looks_like_credentials, scrubbed_env

BASE = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/home/someone",
    "HERMES_HOME": "/home/someone/.hermes-throwaway",
    "HERMES_PROFILE": "review",
    "GH_TOKEN": "ghp_x",
    "GITHUB_TOKEN": "ghp_y",
    "ANTHROPIC_API_KEY": "sk-x",
    "OPENAI_API_KEY": "sk-y",
    "AWS_SECRET_ACCESS_KEY": "z",
    "DB_PASSWORD": "p",
    "SOME_SECRET": "s",
}


class TheSelectedInstallSurvivesTheScrubTests(unittest.TestCase):
    def test_hermes_home_is_passed_through(self) -> None:
        """The bug Kensei hit: the review ran on the main install because this was dropped."""
        self.assertEqual(scrubbed_env(BASE)["HERMES_HOME"], "/home/someone/.hermes-throwaway")

    def test_the_profile_is_passed_through(self) -> None:
        self.assertEqual(scrubbed_env(BASE)["HERMES_PROFILE"], "review")

    def test_a_child_process_really_receives_it(self) -> None:
        """Proven by actually spawning: reading the dict is not the same as the env reaching a subprocess."""
        import subprocess

        from code_shipper.harness.adapters import HermesHarness  # noqa: F401  (import is the point)

        result = subprocess.run(
            ["/bin/sh", "-c", "printf %s \"$HERMES_HOME\""],
            env=scrubbed_env(BASE), capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.stdout, "/home/someone/.hermes-throwaway")

    def test_credentials_are_still_dropped(self) -> None:
        out = scrubbed_env(BASE)
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                    "AWS_SECRET_ACCESS_KEY", "DB_PASSWORD", "SOME_SECRET"):
            self.assertNotIn(key, out, f"{key} leaked into the child environment")

    def test_a_selection_key_pointing_at_credentials_is_dropped(self) -> None:
        """Passing the *path* to a key file would smuggle back what the scrub removes."""
        out = scrubbed_env({**BASE, "HERMES_CONFIG": "/home/someone/.hermes/credentials.json",
                            "HERMES_HOME": "/home/someone/.hermes/secret"})
        self.assertNotIn("HERMES_CONFIG", out)
        self.assertNotIn("HERMES_HOME", out)

    def test_the_contained_run_keeps_its_own_home(self) -> None:
        """Item 1's second half: passing HERMES_HOME through must not weaken containment.

        The contained worker builds its environment from a literal and does not spread the caller's, so an
        unrelated `export HERMES_HOME` cannot reach it. Asserted on the source, because the property is
        "this dict is not built from os.environ" and there is no runtime value that shows it as clearly.
        """
        import ast
        from pathlib import Path

        source = (Path(__file__).resolve().parent.parent
                  / "code_shipper" / "contain" / "hermes_worker.py").read_text()
        tree = ast.parse(source)
        builder = next(n for n in ast.walk(tree)
                       if isinstance(n, ast.FunctionDef) and n.name == "run_hermes")
        spreads_env = any(
            isinstance(node, ast.Dict) and any(key is None for key in node.keys)
            for node in ast.walk(builder)
        )
        self.assertFalse(spreads_env,
                         "run_hermes must not spread os.environ: containment depends on a literal env")
        # And it does set its own home, to a path inside the run.
        self.assertIn("HERMES_HOME", source)

    def test_selection_keys_are_named_in_one_place(self) -> None:
        self.assertIn("HERMES_HOME", HERMES_SELECTION_KEYS)
        self.assertTrue(HERMES_SELECTION_KEYS.isdisjoint({"PATH", "HOME"}))


class TheCredentialHeuristicIsNotAFilterTests(unittest.TestCase):
    def test_it_recognises_the_obvious_shapes(self) -> None:
        for value in ("/x/.hermes/credentials.json", "/x/.env", "/x/api.key",
                      "/home/u/.hermes/secret", "/x/apikey"):
            self.assertTrue(_looks_like_credentials(value), value)

    def test_it_does_not_swallow_ordinary_homes(self) -> None:
        for value in ("/home/someone/.hermes-throwaway", "/tmp/hermes-home", "/work/hermes-home", ""):
            self.assertFalse(_looks_like_credentials(value), value)

    def test_a_normal_install_is_not_refused(self) -> None:
        """The false-positive direction: refusing a legitimate home would break every review."""
        self.assertIn("HERMES_HOME", scrubbed_env({"HERMES_HOME": "/home/someone/.hermes"}))


if __name__ == "__main__":
    unittest.main()
