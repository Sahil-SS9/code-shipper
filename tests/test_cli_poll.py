"""`code-shipper poll` must get as far as its own checks.

It imported a name that never existed, so every run died with an ImportError before reading a token.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

from code_shipper import cli


class PollStartsTests(unittest.TestCase):
    def test_poll_without_a_token_says_so_instead_of_crashing(self) -> None:
        err = io.StringIO()
        with mock.patch.object(cli, "_read_token_quietly", return_value=""), \
                contextlib.redirect_stderr(err):
            code = cli.main(["poll", "2", "--repo", "o/r"])
        self.assertEqual(code, 2)
        self.assertIn("no GitHub token", err.getvalue())


if __name__ == "__main__":
    unittest.main()
