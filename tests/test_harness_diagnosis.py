"""40% of a run had no diagnosis, and a retry that never happened.

Two gaps, both in the measurement path:

1. `HermesHarness` — the hosted path every measurement uses — returned a bare "harness exited 0" style
   failure for an empty answer with no retry, while the direct-API adapter retried twice. The runner then
   printed "(1 error(s))" and the seed was excluded with no reason attached.
2. The 300s per-lens budget, the actual cause of 8 exclusions in 20, was not settable from the runner.

These tests drive a fake `hermes` executable, so they exercise the real subprocess path rather than
patching the adapter's internals.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from code_shipper.harness.adapters import (
    EMPTY_ANSWER,
    START_FAILED,
    TIMEOUT,
    UNPARSEABLE,
    HermesHarness,
)
from code_shipper.harness.base import AgentTask, Budget

# The real shape, imported rather than re-typed: a hand-written {"findings": []} is not a schema and the
# adapter correctly refused it, which my first version of this test mistook for an adapter bug.
from code_shipper.review.lenses import FINDINGS_SCHEMA  # noqa: E402

LENS_SCHEMA = FINDINGS_SCHEMA
TASK = AgentTask(
    kind="review_lens",
    prompt="review this",
    output_schema=LENS_SCHEMA,
    workdir=None,
    budget=Budget(max_seconds=30, max_turns=1),
)


def fake_hermes(root: str, behaviour: str) -> str:
    """A stand-in `hermes` whose output is controlled by `behaviour`.

    `empty`  -> exit 0 with no output at all
    `valid`  -> exit 0 with the correct JSON shape
    `junk`   -> exit 0 with output that is not the shape
    `fail`   -> non-zero exit
    `slow`   -> sleeps past the budget
    """
    script = textwrap.dedent(
        f"""\
        #!/bin/sh
        cat > /dev/null
        case "{behaviour}" in
          empty) exit 0 ;;
          valid) echo '{{"findings": []}}'; exit 0 ;;
          junk)  echo 'I cannot help with that.'; exit 0 ;;
          fail)  echo 'provider error' >&2; exit 7 ;;
          slow)  sleep 90; exit 0 ;;
        esac
        """
    )
    path = Path(root) / f"hermes-{behaviour}"
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def run(behaviour: str, *, max_seconds: int = 30):
    with tempfile.TemporaryDirectory() as root:
        harness = HermesHarness(hermes_bin=fake_hermes(root, behaviour))
        task = AgentTask(kind="review_lens", prompt="x", output_schema=LENS_SCHEMA, workdir=None,
                         budget=Budget(max_seconds=max_seconds, max_turns=1))
        return harness.run(task, dict(os.environ))


class AnEmptyAnswerIsRetriedOnceTests(unittest.TestCase):
    def test_an_empty_answer_is_retried_and_reported_as_such(self) -> None:
        """The retry must happen. The count is what proves it, so the fake counts its own invocations."""
        with tempfile.TemporaryDirectory() as root:
            counter = Path(root) / "count"
            script = Path(root) / "hermes-counted"
            script.write_text(textwrap.dedent(
                f"""\
                #!/bin/sh
                cat > /dev/null
                n=$(cat {counter} 2>/dev/null || echo 0)
                n=$((n + 1))
                echo "$n" > {counter}
                exit 0
                """
            ))
            script.chmod(script.stat().st_mode | stat.S_IXUSR)
            harness = HermesHarness(hermes_bin=str(script))
            result = harness.run(
                AgentTask(kind="review_lens", prompt="x", output_schema=LENS_SCHEMA,
                          workdir=None, budget=Budget(max_seconds=30, max_turns=1)),
                dict(os.environ),
            )
            calls = int(counter.read_text().strip())
            self.assertFalse(result.ok)
            self.assertEqual(calls, 2, "an empty answer must be retried exactly once")
            self.assertIn(EMPTY_ANSWER, result.error)
            self.assertIn("2 attempts", result.error)

    def test_a_valid_answer_is_not_retried(self) -> None:
        """A retry on every call would double the cost of the whole measurement."""
        with tempfile.TemporaryDirectory() as root:
            counter = Path(root) / "count"
            script = Path(root) / "hermes-va"
            script.write_text(textwrap.dedent(
                f"""\
                #!/bin/sh
                cat > /dev/null
                n=$(cat {counter} 2>/dev/null || echo 0)
                n=$((n + 1))
                echo "$n" > {counter}
                echo '{{"findings": []}}'
                """
            ))
            script.chmod(script.stat().st_mode | stat.S_IXUSR)
            result = HermesHarness(hermes_bin=str(script)).run(
                AgentTask(kind="review_lens", prompt="x", output_schema=LENS_SCHEMA,
                          workdir=None, budget=Budget(max_seconds=30, max_turns=1)),
                dict(os.environ),
            )
            self.assertTrue(result.ok)
            self.assertEqual(int(counter.read_text().strip()), 1)


class EachFailureIsNamedTests(unittest.TestCase):
    """The whole point: a report can now say why a seed was excluded."""

    def test_an_empty_answer_is_named(self) -> None:
        self.assertIn(EMPTY_ANSWER, run("empty").error)

    def test_unparseable_output_is_named_and_not_retried(self) -> None:
        result = run("junk")
        self.assertFalse(result.ok)
        self.assertIn(UNPARSEABLE, result.error)

    def test_a_non_zero_exit_is_named_with_its_code(self) -> None:
        result = run("fail")
        self.assertFalse(result.ok)
        self.assertIn("exit_code_7", result.error)

    def test_a_timeout_says_how_long_it_waited(self) -> None:
        result = run("slow", max_seconds=1)
        self.assertFalse(result.ok)
        self.assertIn(TIMEOUT, result.error)
        self.assertIn("31s", result.error)  # budget + the 30s grace

    def test_a_missing_binary_is_named(self) -> None:
        harness = HermesHarness(hermes_bin="/nonexistent/hermes-xyz")
        result = harness.run(TASK, dict(os.environ))
        self.assertFalse(result.ok)
        self.assertIn(START_FAILED, result.error)


class TheBudgetIsSettableFromTheRunnerTests(unittest.TestCase):
    def test_the_runner_accepts_a_lens_budget(self) -> None:
        repo = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [sys.executable, "-B", str(repo / "scripts" / "run_measurement_parallel.py"), "--help"],
            capture_output=True, text=True, timeout=120, cwd=str(repo),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--max-seconds", result.stdout)
        self.assertIn("lens timeouts", result.stdout)

    def test_the_runner_explains_why_excluded_seeds_were_excluded(self) -> None:
        """The source must print each reason, not just a count."""
        repo = Path(__file__).resolve().parent.parent
        source = (repo / "scripts" / "run_measurement_parallel.py").read_text()
        self.assertIn("lens error(s)) — not counted", source)
        self.assertIn("for reason in reasons", source)


if __name__ == "__main__":
    unittest.main()
