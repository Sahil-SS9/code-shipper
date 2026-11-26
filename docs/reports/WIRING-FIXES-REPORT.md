# Round: five wiring defects, local-ci, and the three breakages it found

Status: **stopping for review.** Commits `6529042`, `def577c`, `53d64bc`, `5a4c7b7`, `ee35258`, `b5530ee`.

Evidence is the local-ci output, not a CI run ID, as instructed.

```
$ ./scripts/local-ci.sh
local-ci  ·  repo /home/kensei/repos/code-shipper
sealed: yes

gates
-----
  PASS   syntax/3.11                  every source file compiles
  PASS   syntax/3.12                  every source file compiles
  PASS   syntax/3.13                  every source file compiles
  PASS   tests/3.11                   Ran 1236 tests in 332.274s OK (skipped=2)

$ python3.12 -m unittest discover -s tests -t . -q
Ran 1236 tests in 323.918s
OK (skipped=2)

$ python3.13 -m unittest discover -s tests -t . -q
Ran 1236 tests in 331.639s
OK (skipped=2)   exit=0
```

## Done / Not done against the five fixes

| # | Item | Status |
|---|---|---|
| 1 | Store the PR title and show it on every surface; test shows "Add clamp() helper" | **Done** — `ReviewState.title`, written at both review sites, read by the loader, shown on status / TUI / desktop |
| 2 | Footer built from real rows or hidden; one empty message | **Done** — footer names the first real row; empty state prints one message and no footer |
| 3 | Only an active watch counts as watched; a reviewed PR shows as reviewed | **Done** — "1 reviewed · not watching (reviews run when you ask)"; the interval only appears when a real watch exists |
| 4 | Merge one root cause reported at two lines, via "Also noted" | **Done** — `merge_by_root_cause` on path + enclosing function + normalised title |
| 5 | Refuse credential locations, not any path under `~/.hermes` | **Done** — `credential_location()`; error names the path and suggests `~/repos/<name>` |

### Item 4, and what is still owed on it

The merge is built and tested. **The 20-PR measurement you asked for — merged vs missed — is not done.**
It needs a batch of seeded PRs and the model endpoint, and I would rather start it deliberately than run it
against a tree I was still repairing. Stated plainly rather than buried: this is the one part of item 4
outstanding.

The rule is narrow on purpose, and that narrowness is the thing the measurement has to test:

- merges: same path, same enclosing function, same normalised title;
- does not merge: different functions, different claims in one function, or top-level code with no
  enclosing `def` — because "somewhere in this file" is not a root cause.

Under-merging leaves a duplicate; over-merging hides a bug. The 20-PR run is what turns that judgement into
a number.

## Done / Not done against the four carried items

| Item | Status |
|---|---|
| `scripts/local-ci.sh` — sealed, 3.11/3.12/3.13, per-gate PASS/FAIL, output replaces the CI run ID | **Done** |
| `ROADMAP.md` v1.1 approvals-check entry linked from the README | **Done** |
| README macOS-untested note plus the Windows one-liner | **Done** — and the README had **no install section at all**, which is the larger gap that finding exposed |
| 20-clean-PR false-confirm run | **Not done** — see above |

### About local-ci.sh

It fails if a gate cannot run, not just if a gate fails — a skipped gate is not a green tick, which is the
failure mode a local CI invites. I proved the syntax gate bites rather than trusting it: wrote a PEP 701
nested f-string, and it reported

```
FAIL   syntax/3.11   _ci_selftest.py:1: f-string: expecting '}'
PASS   syntax/3.12   every source file compiles
PASS   syntax/3.13   every source file compiles
RESULT: FAIL (1 failed, 2 passed, 0 skipped)
```

then passed clean once the file was removed.

## Three breakages the full run found

All three were mine, all three were in commits that looked finished, and none was reachable by the targeted
suite alone:

1. **`ReviewRequest.title` broke the dataclass.** A defaulted field before non-default ones is a
   `TypeError` at class creation — `review/service.py` could not be imported at all after `6529042`.
2. **`apply_root_cause_merge` was never imported.** `engine.py` imported `merge_by_root_cause` and called
   `apply_root_cause_merge`. Every review with a confirmed finding raised `NameError`. The feature was in
   the commit and could not have worked.
3. **The contract probe could not clean up after itself.** `TemporaryDirectory` raises from `__exit__` when
   the tree cannot be removed; the worker runs as uid 65534 and leaves files the host cannot unlink, so a
   passing probe was reported as an error — `OSError: Directory not empty: 'home'`.

The lesson I am taking: a targeted suite proves the files you touched, not the program. Two of these three
were import-time or name-resolution errors that a single full run of the package finds in seconds.

## Process note, honestly

`6529042` through `ee35258` were committed and pushed before any of them had been run as a whole. That is
exactly the pattern that produced three broken commits. The suite was green at 1199 when I started this
round; it is 1236 now, and the difference is mostly tests for code that did not import.

## Still unproven

- The 20-clean-PR false-confirm rate, and the merged-vs-missed count from item 4.
- CI on any commit — the account still cannot run Actions. `local-ci.sh` stands in for it, and it is
  honest about what it can and cannot reach: it runs the same gates, on the same three versions, but not
  on macOS or Windows.
- A real contained fix run on macOS or Windows.
- A real hosted-provider authentication run.
