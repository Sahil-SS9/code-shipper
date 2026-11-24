"""Config tests: the refusals matter more than the happy path.

Several invalid combinations are unsafe rather than merely wrong, and each has a rule that must fire:
agent approval in single-identity mode, agent approval without a verified container, and a spend cap
the adapter cannot enforce. Unknown keys are refused too, because a silently ignored key is how a
safety setting stops applying.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from code_shipper.config import Config, ConfigError, from_dict, load


def minimal(**over) -> dict:
    base: dict = {"version": 1}
    base.update(over)
    return base


class HappyPathTests(unittest.TestCase):
    def test_minimal_config_is_valid(self) -> None:
        config = from_dict(minimal())
        self.assertEqual(config.identity_mode, "single")
        self.assertFalse(config.allow_agent_approval)
        self.assertEqual(config.budget.fix_rounds, 3)

    def test_full_config_is_valid(self) -> None:
        config = from_dict(
            minimal(
                identity_mode="separated",
                allow_agent_approval=True,
                harness="command",
                container={"backend": "docker-rootless", "status": "VERIFIED", "image": "img@sha256:x"},
                budget={"daily_usd": 2.5, "fix_rounds": 2, "pushes_per_day": 5, "wall_clock_minutes": 30},
                repos=[{"slug": "o/r", "watching": True, "labels": ["bot"]}],
                review={"level": "deep"},
                watching=True,
                state_dir="/tmp/state",
            )
        )
        self.assertEqual(config.harness, "command")
        self.assertEqual(config.repos[0].slug, "o/r")
        self.assertEqual(config.review_level, "deep")
        self.assertEqual(str(config.state_dir), "/tmp/state")

    def test_validate_is_chainable(self) -> None:
        self.assertIsInstance(Config().validate(), Config)


class UnknownKeyTests(unittest.TestCase):
    def test_unknown_top_level_key_is_refused(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            from_dict(minimal(allow_agent_aproval=True))  # typo on purpose
        self.assertIn("unknown config keys", str(caught.exception))

    def test_unknown_budget_key_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            from_dict(minimal(budget={"daily_usd_cap": 5}))

    def test_unknown_container_key_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            from_dict(minimal(container={"backend": "x", "sandbox": True}))

    def test_unknown_repo_key_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            from_dict(minimal(repos=[{"slug": "o/r", "watch": True}]))


class IdentityModeTests(unittest.TestCase):
    def test_unknown_identity_mode_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            from_dict(minimal(identity_mode="dual"))

    def test_agent_approval_in_single_mode_is_refused(self) -> None:
        # GitHub will not let an author approve their own PR, so this would never approve anything
        with self.assertRaises(ConfigError) as caught:
            from_dict(minimal(allow_agent_approval=True))
        self.assertIn("separated", str(caught.exception))

    def test_agent_approval_without_a_verified_container_is_refused(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            from_dict(
                minimal(
                    identity_mode="separated",
                    allow_agent_approval=True,
                    container={"status": "UNVERIFIED"},
                )
            )
        self.assertIn("VERIFIED", str(caught.exception))

    def test_agent_approval_on_an_unsupported_backend_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            from_dict(
                minimal(
                    identity_mode="separated",
                    allow_agent_approval=True,
                    container={"status": "UNSUPPORTED"},
                )
            )

    def test_agent_approval_with_a_verified_container_is_allowed(self) -> None:
        config = from_dict(
            minimal(
                identity_mode="separated",
                allow_agent_approval=True,
                container={"status": "VERIFIED"},
            )
        )
        self.assertTrue(config.allow_agent_approval)


class BudgetTests(unittest.TestCase):
    def test_a_cap_the_adapter_cannot_enforce_is_refused(self) -> None:
        # far better to refuse than to run without the cap the user believes is in force
        with self.assertRaises(ConfigError) as caught:
            from_dict(minimal(budget={"daily_usd": 5, "enforceable": False}))
        self.assertIn("enforce", str(caught.exception))

    def test_negative_cap_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            from_dict(minimal(budget={"daily_usd": -1}))

    def test_no_cap_is_allowed(self) -> None:
        config = from_dict(minimal())
        self.assertIsNone(config.budget.daily_usd)

    def test_zero_cap_is_allowed(self) -> None:
        self.assertEqual(from_dict(minimal(budget={"daily_usd": 0})).budget.daily_usd, 0)

    def test_non_positive_rounds_are_refused(self) -> None:
        for field in ("fix_rounds", "pushes_per_day", "wall_clock_minutes"):
            with self.subTest(field=field):
                with self.assertRaises(ConfigError):
                    from_dict(minimal(budget={field: 0}))


class RepoTests(unittest.TestCase):
    def test_repo_slug_must_be_owner_name(self) -> None:
        for bad in ("nope", "a/b/c", "/x", "x/"):
            with self.subTest(slug=bad):
                with self.assertRaises(ConfigError):
                    from_dict(minimal(repos=[{"slug": bad}]))

    def test_repo_entry_needs_a_slug(self) -> None:
        with self.assertRaises(ConfigError):
            from_dict(minimal(repos=[{"watching": True}]))


class LevelTests(unittest.TestCase):
    def test_unknown_review_level_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            from_dict(minimal(review={"level": "thorough"}))

    def test_the_three_levels_are_accepted(self) -> None:
        for level in ("quick", "standard", "deep"):
            with self.subTest(level=level):
                self.assertEqual(from_dict(minimal(review={"level": level})).review_level, level)


class HarnessTests(unittest.TestCase):
    def test_unknown_harness_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            from_dict(minimal(harness="claude"))

    def test_the_three_harnesses_are_accepted(self) -> None:
        for name in ("hermes", "command", "openai_compat"):
            with self.subTest(harness=name):
                self.assertEqual(from_dict(minimal(harness=name)).harness, name)


class LoadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="cs-config-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))

    def test_json_config_loads(self) -> None:
        path = self.dir / "c.json"
        path.write_text(json.dumps(minimal(harness="command")))
        self.assertEqual(load(path).harness, "command")

    def test_toml_config_loads(self) -> None:
        path = self.dir / "c.toml"
        path.write_text('version = 1\nharness = "command"\n')
        self.assertEqual(load(path).harness, "command")

    def test_missing_file_is_named(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            load(self.dir / "nope.json")
        self.assertIn("not found", str(caught.exception))

    def test_invalid_json_is_refused(self) -> None:
        path = self.dir / "bad.json"
        path.write_text("{not json")
        with self.assertRaises(ConfigError):
            load(path)

    def test_unsupported_extension_is_refused(self) -> None:
        path = self.dir / "c.yaml"
        path.write_text("version: 1\n")
        with self.assertRaises(ConfigError) as caught:
            load(path)
        self.assertIn("unsupported", str(caught.exception))


class PathTests(unittest.TestCase):
    def test_env_override_wins(self) -> None:
        from code_shipper.paths import default_state_dir

        self.assertEqual(
            default_state_dir(environ={"CODE_SHIPPER_STATE_DIR": "/tmp/pinned"}),
            Path("/tmp/pinned"),
        )

    def test_hermes_plugin_dir_is_used_when_given(self) -> None:
        from code_shipper.paths import default_state_dir

        self.assertEqual(
            default_state_dir(environ={}, hermes_plugin_dir=Path("/h/plugin-data/code-shipper")),
            Path("/h/plugin-data/code-shipper"),
        )

    def test_xdg_state_home_is_respected(self) -> None:
        from code_shipper.paths import default_state_dir

        if __import__("sys").platform != "linux":
            self.skipTest("linux only")
        self.assertEqual(
            default_state_dir(environ={"XDG_STATE_HOME": "/xdg"}),
            Path("/xdg/code-shipper"),
        )


if __name__ == "__main__":
    unittest.main()
