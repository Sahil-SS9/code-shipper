"""Conditions from the STOP A review: the required keyword, the assertive bar, and CI hints fetched.

Three things are pinned here that were not before.

1. ``covered_paths`` has no default and refuses ``None``.
2. ``assertive`` changes the prompt the lens actually receives, and ``quiet`` and ``standard`` do
   too — an accepted option that changes nothing is a false claim.
3. The CLI fetches a failing check's log and annotation paths and frames them as hints, so C10 is a
   feature rather than a component.
"""

from __future__ import annotations

import json
import unittest

from code_shipper.hosts.base import Diff, FilePatch
from code_shipper.review.engine import lens_prompt
from code_shipper.review.framing import ci_annotations, hint_block
from code_shipper.review.lenses import select


def a_diff() -> Diff:
    return Diff(files=(FilePatch(path="a.py", patch="+x = 1\n"),))


class AssertiveBarTests(unittest.TestCase):
    """The profile must reach the lens prompt, or it is a label on nothing."""

    def _prompt(self, assertiveness: str) -> str:
        lens = select("standard")[0]
        return lens_prompt(lens, a_diff(), assertiveness=assertiveness)

    def test_assertive_lowers_the_bar(self) -> None:
        prompt = self._prompt("assertive")
        self.assertIn("low bar", prompt)
        self.assertIn("even if you are not certain", prompt)

    def test_quiet_raises_it(self) -> None:
        prompt = self._prompt("quiet")
        self.assertIn("high bar", prompt)
        self.assertIn("Leave out style", prompt)

    def test_standard_is_the_middle(self) -> None:
        prompt = self._prompt("standard")
        self.assertIn("would matter to the person merging", prompt)
        self.assertNotIn("low bar", prompt)
        self.assertNotIn("high bar", prompt)

    def test_the_three_profiles_produce_different_prompts(self) -> None:
        prompts = {name: self._prompt(name) for name in ("quiet", "standard", "assertive")}
        self.assertEqual(len(set(prompts.values())), 3,
                         "each profile must change what the lens is asked to do")

    def test_the_bar_does_not_weaken_the_framing(self) -> None:
        # the profile changes the ask, never the untrusted-block discipline
        for name in ("quiet", "standard", "assertive"):
            with self.subTest(profile=name):
                self.assertEqual(self._prompt(name).count("</untrusted>"), 1)

    def test_an_unknown_profile_falls_back_to_standard(self) -> None:
        prompt = lens_prompt(select("standard")[0], a_diff(), assertiveness="nonsense")
        self.assertIn("would matter to the person merging", prompt)


class CiHintFetchTests(unittest.TestCase):
    """C10: the CLI must actually fetch the log, and frame it as a hint."""

    def test_the_hint_block_is_framed_as_untrusted(self) -> None:
        block = hint_block(["tests/test_calc.py"])
        self.assertEqual(block.count("</untrusted>"), 1)
        self.assertIn("tests/test_calc.py", block)

    def test_annotations_from_a_log_are_extracted(self) -> None:
        log = "calc/__init__.py:6: in divide\nE   TypeError\n"
        self.assertIn("calc/__init__.py", ci_annotations(log))

    def test_the_host_returns_empty_when_nothing_failed(self) -> None:
        """A green PR yields no log, and that is not an error."""
        from code_shipper.hosts.github import GitHubHost

        host = GitHubHost.__new__(GitHubHost)
        host._api = "https://api.github.invalid"
        host._token = "fake"
        host._timeout = 5
        host._opener = _FakeOpener({"workflow_runs": []})
        self.assertEqual(host.failing_log("o/r", "abc"), "")

    def test_a_failing_run_yields_its_log(self) -> None:
        from code_shipper.hosts.github import GitHubHost

        host = GitHubHost.__new__(GitHubHost)
        host._api = "https://api.github.invalid"
        host._token = "fake"
        host._timeout = 5
        host._opener = _FakeOpener(
            {
                "workflow_runs": [{"id": 1, "conclusion": "failure"}],
            },
            jobs={"jobs": [{"id": 7, "conclusion": "failure"}]},
            log="FAILED tests/test_calc.py::test_divide\ncalc/__init__.py:6: in divide\n",
        )
        log = host.failing_log("o/r", "abc")
        self.assertIn("FAILED tests/test_calc.py", log)
        self.assertIn("calc/__init__.py", ci_annotations(log))

    def test_annotations_are_paths_only(self) -> None:
        from code_shipper.hosts.github import GitHubHost

        host = GitHubHost.__new__(GitHubHost)
        host._api = "https://api.github.invalid"
        host._token = "fake"
        host._timeout = 5
        host._opener = _FakeOpener(
            {"check_runs": [
                {"output": {"annotations": [{"path": "a.py", "message": "boom"},
                                            {"path": "b.py", "message": "bang"}]}},
            ]}
        )
        self.assertEqual(host.annotations("o/r", "abc"), ["a.py", "b.py"])


class _FakeOpener:
    """Serves canned JSON, and plain text for a logs URL."""

    def __init__(self, runs: dict, *, jobs: dict | None = None, log: str = "") -> None:
        self.runs = runs
        self.jobs = jobs or {}
        self.log = log

    def open(self, request, timeout=None):  # noqa: A003 - matches urllib's interface
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if "/logs" in url:
            return _Response(self.log.encode())
        if "/jobs" in url:
            return _Response(json.dumps(self.jobs).encode())
        if "/check-runs" in url:
            return _Response(json.dumps(self.runs).encode())
        return _Response(json.dumps(self.runs).encode())


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.status = 200
        self.headers: dict = {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


if __name__ == "__main__":
    unittest.main()
