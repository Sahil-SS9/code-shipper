"""The installed entry point.

Exists so `code-shipper` works as a console script and as `python -m code_shipper`, both routing to
one implementation. Keeping this separate from cli.py means the packaging hook has a stable target
even if the argument parsing is reorganised.
"""

from __future__ import annotations

import sys

from .cli import main


def run() -> int:
    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(run())
