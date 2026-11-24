"""Seed five sandbox PRs, each with one deliberate bug, for the dry-run review test.

Every branch carries exactly one seeded defect in calc/, so a review that reports nothing is visibly
wrong rather than plausibly quiet. Nothing here posts a review: seeding only creates branches and PRs
on the sandbox repository.

  python3 spikes/b1/seed_prs.py            # create or update the five PRs
  python3 spikes/b1/seed_prs.py --json     # report what exists
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SANDBOX = "Sahil-SS9/code-shipper-sandbox"
CLONE = Path.home() / "repos" / "code-shipper-sandbox"

BASE = '''"""Small arithmetic helpers used by Code-Shipper's sandbox."""


def add(a, b):
    return a + b


def divide(a, b):
    if b == 0:
        raise ZeroDivisionError("b must not be zero")
    return a / b
'''

# Each entry is one seeded defect. The bug is real code, not a marker: a reviewer has to read the
# change to find it. All five touch the two functions main actually has.
SEEDS = [
    {
        "branch": "seed/off-by-one",
        "title": "Refactor divide for readability",
        "body": "Extracts the zero check. Behaviour should be unchanged.",
        "bug": "off-by-one guard",
        "file": "calc/__init__.py",
        "old": "    if b == 0:",
        "new": "    if b <= 0:",
        "note": "rejects negative divisors, which is a behaviour change disguised as a refactor",
    },
    {
        "branch": "seed/wrong-divide",
        "title": "Simplify divide to a single expression",
        "body": "Collapses the function body.",
        "bug": "wrong operator",
        "file": "calc/__init__.py",
        "old": "    return a / b",
        "new": "    return a * b",
        "note": "multiplication where division was meant: every caller gets a wrong number",
    },
    {
        "branch": "seed/swallowed-error",
        "title": "Improve divide error handling",
        "body": "Makes divide tolerant of a zero denominator.",
        "bug": "swallowed error",
        "file": "calc/__init__.py",
        "old": '        raise ZeroDivisionError("b must not be zero")',
        "new": "        return 0  # a zero denominator is treated as zero",
        "note": "returns a wrong answer instead of raising, hiding the caller's bug",
    },
    {
        "branch": "seed/wrong-add",
        "title": "Micro-optimise add()",
        "body": "Uses a tuple sum for speed.",
        "bug": "wrong operator",
        "file": "calc/__init__.py",
        "old": "    return a + b",
        "new": "    return a - b",
        "note": "subtraction where addition was meant; the function name and its test disagree",
    },
    {
        "branch": "seed/type-confusion",
        "title": "Coerce add() inputs to strings",
        "body": "Handles mixed input types by normalising to strings first.",
        "bug": "type confusion",
        "file": "calc/__init__.py",
        "old": "    return a + b",
        "new": '    return str(a) + str(b)  # normalise mixed input types',
        "note": "turns add(2, 3) into '23'; plausible-looking defensiveness that breaks arithmetic",
    },
]


def run(argv: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise SystemExit(f"FAILED {' '.join(argv[:3])}: {(proc.stderr or proc.stdout)[-400:]}")
    return proc


def gh_json(*args: str):
    return json.loads(run(["gh", *args, "-R", SANDBOX]).stdout or "null")


def seed_one(seed: dict) -> dict:
    branch = seed["branch"]
    run(["git", "fetch", "-q", "origin"], cwd=CLONE)
    run(["git", "checkout", "-q", "-B", branch, "origin/main"], cwd=CLONE)

    target = CLONE / seed["file"]
    text = target.read_text()
    if seed["old"] not in text:
        raise SystemExit(f"{branch}: anchor not found in {seed['file']}: {seed['old']!r}")
    target.write_text(text.replace(seed["old"], seed["new"], 1))

    run(["git", "commit", "-q", "-am", seed["title"]], cwd=CLONE)
    head = run(["git", "rev-parse", "HEAD"], cwd=CLONE).stdout.strip()
    run(["git", "push", "-q", "-f", "origin", branch], cwd=CLONE)

    existing = gh_json("pr", "list", "--head", branch, "--state", "open", "--json", "number")
    if existing:
        number = existing[0]["number"]
    else:
        url = run(
            ["gh", "pr", "create", "-R", SANDBOX, "--head", branch, "--base", "main",
             "--title", seed["title"], "--body", seed["body"]]
        ).stdout.strip()
        number = int(url.rsplit("/", 1)[1])

    return {"pr": number, "branch": branch, "head": head, "bug": seed["bug"], "note": seed["note"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    results = [seed_one(seed) for seed in SEEDS]
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for entry in results:
            print(f"PR #{entry['pr']:3}  {entry['branch']:26} {entry['bug']}")
            print(f"         {entry['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
