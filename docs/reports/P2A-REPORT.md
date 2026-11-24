# P2-A — Review engine: lenses, verification, ranking, dry run

Status: complete, awaiting review. Nothing was posted; every review below was a dry run.
Commit: `c16a3bf`

## What was built

| Module | What it does |
|---|---|
| `review/lenses.py` | Lens prompts and output schemas. Level selection per K7. |
| `review/verify.py` | The verification pass: refute each finding, keep only `confirmed`. |
| `review/rank.py` | De-duplication, ordering, and the inline posting cap. |
| `review/engine.py` | Drives lenses → verification → ranking. Contains no way to post. |
| `cli.py review` | `review <pr> --repo X --level L --dry-run` |

**Lenses.** Quick is one *combined* lens, not a truncated Deep. That is a deliberate difference: a
truncated Deep would produce a vague pass with none of the specialist sections, which reads as a
weaker review of the same change rather than a cheaper one.

**Verification runs at every level, Quick included** (FR-R2). Only `confirmed` findings are reported.
`refuted` and `unsure` are recorded and never posted, and a finding the verifier did not mention is
dropped too — silence is not confirmation. The prompt asks the verifier to *refute*, because asking
"is this right?" invites agreement.

**No posting path exists in the engine.** It returns a plan; the caller decides. That is what makes
`--dry-run` trustworthy: it is the same code as a real review, minus one call that lives elsewhere.
This is also why `--dry-run` is not a separate code path that could drift.

**A failed lens marks the review incomplete** and says so. It never reports "no issues" when a lens
did not run (T-ADV-05).

## Five seeded PRs

Seeded on `Sahil-SS9/code-shipper-sandbox`, each with one real defect — not a marker comment, so a
review has to read the code:

| PR | Branch | Seeded defect |
|---|---|---|
| #18 | `seed/off-by-one` | `if b == 0` → `if b <= 0`: rejects negative divisors, disguised as a refactor |
| #19 | `seed/wrong-divide` | `a / b` → `a * b` |
| #20 | `seed/swallowed-error` | `raise ZeroDivisionError` → `return 0` |
| #21 | `seed/wrong-add` | `a + b` → `a - b` |
| #22 | `seed/type-confusion` | `a + b` → `str(a) + str(b)` |

## Results (T-ADV-05)

Level `standard` (3 lenses), all five complete, nothing posted:

| PR | Raw findings | Confirmed | Posted after de-dup | Finding |
|---|---|---|---|---|
| #18 | 2 | 2 | 1 | `calc/__init__.py:6` divide() guard changed from `b == 0` to `b <= 0`, so every negative divisor is now rejected |
| #19 | 2 | 2 | 1 | `calc/__init__.py:8` divide() now multiplies: returns `a * b` instead of `a / b` |
| #20 | 3 | 2 | 1 | `calc/__init__.py:7` divide() now returns 0 for a zero denominator instead of raising |
| #21 | 2 | 2 | 1 | `calc/__init__.py:2` add() now subtracts: `add(2, 3)` returns `-1` instead of `5` |
| #22 | 2 | 2 | 1 | `calc/__init__.py:2` add() now concatenates strings instead of adding numbers |

**Five seeded bugs, five confirmed findings, correct line in every case.** PR #18's is the one worth
reading closely: the change looks like a tidy refactor and the finding explains why it is a behaviour
change for every caller passing a negative number.

The raw counts exceed the confirmed counts because each level's lenses describe the same bug in
different words. Two lenses finding one problem is expected; reporting it twice is not.

## A defect this run found, and fixed

The first pass posted **two findings on PR #22** for one bug: the `edge` lens said "concatenates
string forms instead of adding", the `correctness` lens said "concatenates strings instead of adding
numbers". De-duplication keyed on an exact title, so both survived.

Same path and line is the same problem, so location is now part of the key and the highest-ranked
finding wins. Re-running all five: `posted 1` each, as the table shows. Pinned by a test that uses the
PR #22 wording verbatim, so this specific regression cannot come back.

## Not done in this checkpoint

- **Nothing posted.** `review` without `--dry-run` refuses with "posting is not implemented yet
  (P2-B)".
- **The verification rate is not measured.** This shows the engine finds seeded bugs; it does not
  show how often it wrongly confirms a finding that is not real. That needs seeded *clean* PRs with
  known-good changes, which is Phase 7's T-EVAL-SEEDED.
- **No review comments or CI logs are in the prompt yet** beyond the diff. The engine accepts a
  `log_tail`, and the CLI does not pass one.
- **Cost and time are unmeasured.** Each `standard` review is 3 lens calls plus verification; a run
  takes roughly a minute against the local model.

## Reproduce

```
cd /home/kensei/repos/code-shipper
./scripts/run_tests.sh                       # 363 tests

python3 spikes/b1/seed_prs.py                # create/refresh the five seeded PRs
python3 -m code_shipper.cli review 19 --repo Sahil-SS9/code-shipper-sandbox --level quick --dry-run
```

Receipts: `docs/reports/evidence/P2/dryrun-pr{18..22}.json`.

## Test output (real)

```
$ ./scripts/run_tests.sh
Ran 363 tests in 109.750s

OK
```

## CI

Checkpoint run **45501480350** (head `9ac343d`) — all six jobs success. Same matrix as the
previous green run 45496877281.

| Job | Result |
|---|---|
| ubuntu-latest, py3.11 | success |
| ubuntu-latest, py3.12 | success |
| macos-latest, py3.11 | success |
| macos-latest, py3.12 | success |
| windows-latest, py3.11 | success |
| windows-latest, py3.12 | success |

The earlier run (45495878949) failed on all three platforms and exposed a real defect: the scope
rules matched `/` against os-native paths, so on Windows `calc\\conftest.py` matched no forbidden
pattern and would have been accepted. Fixed and pinned before this checkpoint.
