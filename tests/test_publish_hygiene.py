"""Nothing tracked names the build machine: no home directory, no private host address."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Built from parts so this file does not match itself.
BUILD_HOME = "/home/" + "kensei"
PRIVATE_HOST = re.compile(r"\b100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b")  # tailnet range


class NoMachineDetailsTests(unittest.TestCase):
    def tracked(self) -> list[Path]:
        try:
            out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True,
                                 timeout=30).stdout.decode()
        except (OSError, subprocess.SubprocessError):
            self.skipTest("not a git checkout")
        return [ROOT / p for p in out.split("\0") if p]

    def test_no_tracked_text_file_names_the_build_home_or_a_private_host(self) -> None:
        offenders = []
        for path in self.tracked():
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if BUILD_HOME in text or PRIVATE_HOST.search(text):
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
