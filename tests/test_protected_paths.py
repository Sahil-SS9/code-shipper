"""N1: rule files can never be edited by a fix, whatever the configuration says.

Before this, `ScopePolicy.forbidden` defaulted to empty, so `allowed: ["**"]` let a fix edit CI, CODEOWNERS
and Code-Shipper's own rules. Worse, a repository that *did* forbid `.github/**` was not protected either:
the path cleaner used `lstrip("./")`, which turned ".github/ci.yml" into "github/ci.yml", so the pattern
never matched. Both are pinned here.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from code_shipper.contain.fixloop import check_scope, snapshot
from code_shipper.steward import policy as policy_module
from code_shipper.steward.policy import (
    PROTECTED_PATHS,
    ScopePolicy,
    normalise_path,
    protected_reason,
)

EVERYTHING = ScopePolicy(allowed=("**",))

# One concrete path per protected class.
EXAMPLES = {
    ".github/workflows/ci.yml": ".github/**",
    "CODEOWNERS": "CODEOWNERS",
    "docs/CODEOWNERS": "CODEOWNERS",
    ".code-shipper/approvals.yml": ".code-shipper/**",
    "code-shipper.yml": "code-shipper.yml",
    "AGENTS.md": "AGENTS.md",
    "pkg/AGENTS.md": "AGENTS.md",
    "CLAUDE.md": "CLAUDE.md",
    "REVIEW.md": "REVIEW.md",
    ".githooks/pre-push": ".githooks/**",
    ".husky/pre-commit": ".husky/**",
    ".gitmodules": ".gitmodules",
    ".env": ".env",
    ".env.production": ".env.*",
    "deploy/server.pem": "*.pem",
    "certs/tls.key": "*.key",
    "id_rsa": "id_rsa*",
    "config/aws_credentials.json": "*credentials*",
}


class EveryClassIsRefusedEvenWhenEverythingIsAllowedTests(unittest.TestCase):
    def test_each_example_is_protected_by_the_expected_rule(self) -> None:
        for path, rule in EXAMPLES.items():
            with self.subTest(path=path):
                self.assertEqual(protected_reason(path), rule)
                self.assertFalse(EVERYTHING.path_allowed(path))

    def test_case_and_prefix_variants_do_not_slip_through(self) -> None:
        for path in (".GitHub/workflows/ci.yml", "./.github/x.yml", "/.code-shipper/a.yml",
                     ".github\\workflows\\ci.yml", "Agents.md", "codeowners"):
            with self.subTest(path=path):
                self.assertTrue(protected_reason(path), path)

    def test_ordinary_source_is_still_allowed(self) -> None:
        for path in ("src/app.py", "calc/__init__.py", "tests/test_calc.py", "docs/guide.md",
                     "github/notes.md", "environment.py"):
            with self.subTest(path=path):
                self.assertEqual(protected_reason(path), "")
                self.assertTrue(EVERYTHING.path_allowed(path))


class ConfigurationCannotShrinkTheListTests(unittest.TestCase):
    def test_scope_policy_has_no_way_to_name_the_list(self) -> None:
        fields = set(ScopePolicy.__dataclass_fields__)
        self.assertEqual(fields, {"allowed", "forbidden", "tests", "max_files", "max_total_bytes"})
        with self.assertRaises(ValueError):
            ScopePolicy.from_config({"allowed": ["**"], "protected": []})

    def test_an_empty_or_unrelated_forbidden_list_changes_nothing(self) -> None:
        for forbidden in ([], ["src/legacy/**"]):
            policy = ScopePolicy.from_config({"allowed": ["**"], "forbidden": forbidden})
            for path in EXAMPLES:
                with self.subTest(forbidden=forbidden, path=path):
                    self.assertFalse(policy.path_allowed(path))


class ARepositorysOwnForbiddenListNowWorksTests(unittest.TestCase):
    """The lstrip bug: a configured dot-path pattern never matched."""

    def test_leading_dots_survive_normalisation(self) -> None:
        self.assertEqual(normalise_path(".github/ci.yml"), ".github/ci.yml")
        self.assertEqual(normalise_path(".env"), ".env")
        self.assertEqual(normalise_path("./src/a.py"), "src/a.py")
        self.assertEqual(normalise_path("././/src/a.py"), "src/a.py")

    def test_a_configured_dot_pattern_is_honoured(self) -> None:
        policy = ScopePolicy(allowed=("**",), forbidden=(".storybook/**",))
        self.assertFalse(policy.path_allowed(".storybook/main.js"))
        self.assertTrue(policy.path_allowed("storybook/main.js"))


class TheRealScopeCheckRefusesAndNamesThePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cs-protected-"))
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x = 1\n")
        self.before = snapshot(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_a_fix_touching_ci_is_refused_by_name(self) -> None:
        (self.root / ".github" / "workflows").mkdir(parents=True)
        (self.root / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
        (self.root / "src" / "a.py").write_text("x = 2\n")
        verdict = check_scope(self.before, self.root, policy=EVERYTHING)
        self.assertFalse(verdict.ok)
        self.assertTrue(any(".github/workflows/ci.yml" in p and "protected" in p for p in verdict.problems),
                        verdict.problems)

    def test_the_same_fix_without_the_protected_file_passes(self) -> None:
        (self.root / "src" / "a.py").write_text("x = 2\n")
        verdict = check_scope(self.before, self.root, policy=EVERYTHING)
        self.assertTrue(verdict.ok, verdict.problems)


class MutationTests(unittest.TestCase):
    def test_emptying_the_list_lets_the_fix_through(self) -> None:
        """If the list is what stops it, removing the list must let it through."""
        with mock.patch.object(policy_module, "PROTECTED_PATHS", ()):
            self.assertEqual(protected_reason(".github/workflows/ci.yml"), "")
            self.assertTrue(EVERYTHING.path_allowed(".github/workflows/ci.yml"))
        self.assertFalse(EVERYTHING.path_allowed(".github/workflows/ci.yml"))

    def test_the_list_covers_every_class_the_handoff_names(self) -> None:
        for required in (".github/**", "CODEOWNERS", "**/CODEOWNERS", ".code-shipper/**", "code-shipper.yml",
                         "AGENTS.md", "CLAUDE.md", "REVIEW.md", ".git/**", ".githooks/**", ".husky/**",
                         ".env.*", "*.pem", "*.key", "id_rsa*", "*credentials*"):
            self.assertIn(required, PROTECTED_PATHS)


if __name__ == "__main__":
    unittest.main()
