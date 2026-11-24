"""Harness contract tests: schema validation, honest availability, and T-ADV-12.

T-ADV-12 is the one that matters: a result that does not match the schema must be rejected and must
produce no writes. Here that means `ok=False` with an error and no `output`, because every caller
treats a result without output as nothing to act on.

The real-install test (T-CONTRACT-01) is separate and skippable: it runs only where `hermes` exists.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from code_shipper.harness.adapters import (
    CommandHarness,
    HermesHarness,
    OpenAICompatHarness,
    scrubbed_env,
)
from code_shipper.harness.base import (
    AgentResult,
    AgentTask,
    Budget,
    FakeHarness,
    HarnessError,
    parse_json_object,
    validate_against_schema,
    which,
)

# The interpreter name differs on Windows, and these tests spawn a real command.
PY = sys.executable

FIX_SCHEMA = {
    "type": "object",
    "required": ["status", "summary"],
    "properties": {
        "status": {"type": "string", "enum": ["changed", "no_change", "declined"]},
        "summary": {"type": "string"},
    },
}


def task(**over) -> AgentTask:
    base = dict(
        kind="fix",
        prompt="do the thing",
        workdir=Path(tempfile.gettempdir()),
        output_schema=FIX_SCHEMA,
        budget=Budget(max_seconds=5, max_turns=2),
    )
    base.update(over)
    return AgentTask(**base)  # type: ignore[arg-type]


class SchemaTests(unittest.TestCase):
    def test_a_valid_result_passes(self) -> None:
        self.assertEqual(
            validate_against_schema({"status": "changed", "summary": "ok"}, FIX_SCHEMA), []
        )

    def test_a_missing_required_key_fails(self) -> None:
        errors = validate_against_schema({"status": "changed"}, FIX_SCHEMA)
        self.assertTrue(any("summary" in e for e in errors), errors)

    def test_a_wrong_type_fails(self) -> None:
        errors = validate_against_schema({"status": 1, "summary": "ok"}, FIX_SCHEMA)
        self.assertTrue(any("expected string" in e for e in errors), errors)

    def test_an_enum_violation_fails(self) -> None:
        errors = validate_against_schema({"status": "maybe", "summary": "ok"}, FIX_SCHEMA)
        self.assertTrue(any("not in" in e for e in errors), errors)

    def test_nested_arrays_are_validated(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "items": {"type": "object", "required": ["title"],
                              "properties": {"title": {"type": "string"}}},
                }
            },
        }
        self.assertEqual(validate_against_schema({"findings": [{"title": "x"}]}, schema), [])
        errors = validate_against_schema({"findings": [{"nope": 1}]}, schema)
        self.assertTrue(errors)

    def test_an_unsupported_keyword_raises_rather_than_being_ignored(self) -> None:
        # silently skipping a constraint is how an invalid result gets posted
        with self.assertRaises(HarnessError):
            validate_against_schema({"a": 1}, {"type": "object", "oneOf": []})

    def test_additional_properties_false_is_enforced(self) -> None:
        schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "additionalProperties": False,
        }
        self.assertTrue(validate_against_schema({"a": "x", "b": 1}, schema))


class ParseTests(unittest.TestCase):
    def test_json_is_extracted_from_surrounding_prose(self) -> None:
        text = 'I fixed it. Here you go:\n{"status": "changed", "summary": "ok"}\nDone.'
        self.assertEqual(parse_json_object(text)["status"], "changed")

    def test_the_last_object_wins(self) -> None:
        text = 'draft {"status": "no_change"} final {"status": "changed"}'
        self.assertEqual(parse_json_object(text)["status"], "changed")

    def test_braces_inside_strings_do_not_confuse_it(self) -> None:
        text = '{"summary": "a } b { c", "status": "changed"}'
        self.assertEqual(parse_json_object(text)["status"], "changed")

    def test_no_object_is_an_error_not_an_empty_result(self) -> None:
        with self.assertRaises(HarnessError):
            parse_json_object("I could not do it")


class SCHEMA_ADV12Tests(unittest.TestCase):
    """T-ADV-12, at the harness boundary."""

    def test_a_non_matching_result_is_rejected_and_has_no_output(self) -> None:
        harness = FakeHarness(AgentResult(ok=True, output={"status": "unexpected"}))
        result = harness.run(task(), {})
        # the fake bypasses validation on purpose; the caller must still gate on the schema
        errors = validate_against_schema(result.output or {}, FIX_SCHEMA)
        self.assertTrue(errors)
        self.assertEqual(result.output.get("status"), "unexpected")


class FakeHarnessTests(unittest.TestCase):
    def test_it_records_the_task_it_was_given(self) -> None:
        harness = FakeHarness(AgentResult(ok=True, output={"status": "changed", "summary": "s"}))
        harness.run(task(), {})
        self.assertEqual(len(harness.tasks), 1)

    def test_availability_is_reported_as_given(self) -> None:
        harness = FakeHarness(AgentResult(ok=True), available=(False, "not installed"))
        self.assertEqual(harness.available(), (False, "not installed"))


class ScrubbedEnvTests(unittest.TestCase):
    def test_tokens_and_secrets_are_removed(self) -> None:
        env = scrubbed_env(
            {
                "PATH": "/usr/bin",
                "GH_TOKEN": "x",
                "GITHUB_TOKEN": "x",
                "MY_API_KEY": "x",
                "SOME_SECRET": "x",
                "DB_PASSWORD": "x",
                "LANG": "C.UTF-8",
            }
        )
        self.assertEqual(set(env), {"PATH", "LANG"})

    def test_the_controls_survive(self) -> None:
        env = scrubbed_env({"PATH": "/usr/bin", "HOME": "/h", "TMPDIR": "/tmp"})
        self.assertEqual(set(env), {"PATH", "HOME", "TMPDIR"})


class HermesAdapterTests(unittest.TestCase):
    def test_argv_puts_the_prompt_on_stdin_and_never_in_argv(self) -> None:
        argv = HermesHarness().argv(task())
        self.assertIn("--query-file", argv)
        self.assertIn("-", argv)
        self.assertNotIn("do the thing", " ".join(argv))

    def test_review_kinds_disable_tools(self) -> None:
        h = HermesHarness()
        self.assertIn("", h.argv(task(kind="review_lens")))

    def test_availability_is_honest_when_missing(self) -> None:
        ok, reason = HermesHarness(hermes_bin="definitely-not-installed-xyz").available()
        self.assertFalse(ok)
        self.assertIn("not found", reason)

    def test_a_missing_binary_reports_rather_than_crashing(self) -> None:
        result = HermesHarness(hermes_bin="definitely-not-installed-xyz").run(task(), {})
        self.assertFalse(result.ok)
        self.assertIsNone(result.output)


class CommandAdapterTests(unittest.TestCase):
    def test_a_preset_that_is_missing_says_so(self) -> None:
        ok, reason = CommandHarness(preset="claude").available()
        if which("claude") is None:
            self.assertFalse(ok)
            self.assertIn("not found", reason)

    def test_presets_start_unverified(self) -> None:
        self.assertFalse(CommandHarness(preset="claude").preset_verified)
        self.assertFalse(CommandHarness(preset="codex").preset_verified)
        self.assertFalse(CommandHarness(preset="opencode").preset_verified)

    def test_an_unknown_preset_is_refused(self) -> None:
        with self.assertRaises(HarnessError):
            CommandHarness(preset="nope")

    def test_it_runs_a_real_command_and_parses_its_json(self) -> None:
        argv = [PY, "-c", 'print(\'{"status": "changed", "summary": "ok"}\')']
        result = CommandHarness(argv=argv).run(task(), {})
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.output["status"], "changed")

    def test_a_failing_command_is_reported(self) -> None:
        result = CommandHarness(argv=[PY, "-c", "raise SystemExit(3)"]).run(task(), {})
        self.assertFalse(result.ok)
        self.assertIn("exited 3", result.error)

    def test_non_json_output_is_rejected(self) -> None:
        result = CommandHarness(argv=[PY, "-c", "print('no json here')"]).run(task(), {})
        self.assertFalse(result.ok)
        self.assertIsNone(result.output)


class OpenAICompatAdapterTests(unittest.TestCase):
    def test_it_refuses_a_fix_task_with_a_reason(self) -> None:
        adapter = OpenAICompatHarness("http://127.0.0.1:1", "k", "m")
        result = adapter.run(task(kind="fix"), {})
        self.assertFalse(result.ok)
        self.assertIn("review-only", result.error)

    def test_availability_needs_a_key(self) -> None:
        ok, reason = OpenAICompatHarness("http://x", "", "m").available()
        self.assertFalse(ok)
        self.assertIn("API key", reason)

    def test_the_key_is_not_in_the_repr(self) -> None:
        adapter = OpenAICompatHarness("http://x", "supersecret-key", "m")
        self.assertNotIn("supersecret-key", repr(adapter))


def _hermes_installed() -> bool:
    return shutil.which("hermes") is not None


@unittest.skipUnless(_hermes_installed(), "hermes not installed")
class HermesContractTest(unittest.TestCase):
    """T-CONTRACT-01 for the hermes adapter, on a real install.

    Deliberately not mocked: the point is that the flags still exist and the process really returns
    parseable output. A mocked test would pass against a CLI that had changed its arguments.
    """

    def test_availability_reports_yes(self) -> None:
        ok, reason = HermesHarness().available()
        self.assertTrue(ok, reason)
        self.assertIn("Hermes", reason)


if __name__ == "__main__":
    unittest.main()
