"""Tests for the worker's tool configuration.

The bug these guard against: ``-t ""`` does not mean "no tools". Hermes treats an empty value as
unset, adds every default toolset, and sends ~20 tool definitions to the provider. That is both a
wider surface than the containment claim allows and enough schema to overflow a small local model.

So the workers disable toolsets explicitly, and these tests assert the lists are right. They do not
run a container: they check the configuration the workers will apply.
"""

from __future__ import annotations

import unittest

from code_shipper.contain import fix_worker, hermes_worker


class HermesWorkerToolTests(unittest.TestCase):
    def test_every_builtin_toolset_is_in_the_disable_list(self) -> None:
        expected = {
            "web", "browser", "terminal", "file", "code_execution", "vision", "image_gen",
            "tts", "skills", "todo", "memory", "session_search", "connections", "clarify",
            "delegation", "cronjob", "computer_use",
        }
        self.assertEqual(set(hermes_worker.ALL_TOOLSETS), expected)

    def test_the_dangerous_ones_are_present(self) -> None:
        # the ones that would matter if they leaked through
        for toolset in ("terminal", "browser", "web", "code_execution", "file"):
            self.assertIn(toolset, hermes_worker.ALL_TOOLSETS)


class FixWorkerToolTests(unittest.TestCase):
    def test_file_is_not_disabled(self) -> None:
        self.assertNotIn("file", fix_worker.TOOLSETS_EXCEPT_FILE)

    def test_terminal_and_network_toolsets_are_disabled(self) -> None:
        for toolset in ("terminal", "browser", "web", "code_execution"):
            self.assertIn(toolset, fix_worker.TOOLSETS_EXCEPT_FILE)

    def test_the_two_lists_partition_the_builtin_toolsets(self) -> None:
        # every toolset is either disabled by both, or disabled by all but the fix worker's `file`
        self.assertEqual(
            set(hermes_worker.ALL_TOOLSETS) - set(fix_worker.TOOLSETS_EXCEPT_FILE),
            {"file"},
        )


if __name__ == "__main__":
    unittest.main()
