# Precision fixes 2–4 + amendment 1b, with the recall guard

Run 2026-11-26. Repo `Sahil-SS9/code-shipper`, branch `main`.

## Commits

| Commit | Fix | Change |
|---|---|---|
| `60700df` | 1 | `SCOPE_RULE` composed into all six lenses; `Lens.__post_init__` refuses an unscoped lens |
| `ae9e2b7` | 1b | Amendment 1b — three separate grounds for a finding, so the security lens is not gagged |
| `8f1e6ef` | 2 | `review/contract.py`; the docstrings reach both the lenses and the verifier |
| `35fe61d` | 3 | review memory wired read + write; `lessons add` |
| `9d240ff` | 4 | per-failure diagnosis in `HermesHarness`; empty-answer retry; `--max-seconds` |

Each is its own commit with its own tests, and `local-ci` was green before every push.

## local-ci

```
syntax/3.11 PASS   every source file compiles
syntax/3.12 PASS   every source file compiles
syntax/3.13 PASS   every source file compiles
tests/3.11  PASS   Ran 1335 tests OK (skipped=4)
tests/3.12  PASS   Ran 1335 tests OK (skipped=4)
tests/3.13  PASS   Ran 1335 tests OK (skipped=4)
imports     PASS   every shipped module imports cleanly
seal        PASS   run dir /tmp/local-ci.eZA9LF (base /tmp), no credentials read
RESULT: PASS (8 passed, 0 skipped)
```

Suite at `9d240ff`: **1344 tests OK (skipped=3)**.

## Amendment 1b

The rule as first written required *"the line of the contract or docstring the change violates"* and called
*"a request for extra input validation"* an enhancement. Inside the **security** lens that suppresses real
bugs — SQL injection, path traversal and a leaked secret almost never contradict a docstring, and a wrong
operator in undocumented code has no docstring line to point to. It now names three separate grounds:

> contradicts the docstring or stated intent, contradicts what the name and the callers clearly expect, or
> introduces a security vulnerability

Structural enforcement is unchanged. The substance tests pin both directions: security must stay in scope,
and the two over-reaching phrases must not come back.

## The 20-seed re-run

`python3 scripts/run_measurement_parallel.py --workers 2 --max-seconds 600`
Evidence: `docs/reports/evidence/final-run.json`, log `/tmp/final-run.log`.

```
attempted   : 20
evaluated   : 20
unevaluated : 0
discarded   : 0
errored     : 0
findings    : 7 across 4 seed(s)
rate        : 20% of evaluated seeds (4/20)
```

**Excluded: none, for any reason.** Fix 4 closed the gap — the previous run excluded 8 of 20 on lens
timeouts at the 300 s budget; at 600 s every seed evaluated. That is a real improvement in coverage and it
makes this the first run where the rate is computed over the whole seed set rather than a surviving subset.

### Adjudication by execution — 7 of 7 real, 0 false

Every finding was reproduced against the seed code, not accepted from its description.

| Seed | Line | Claim | Verdict |
|---|---|---|---|
| slugify | 23 | Latin letters without an NFKD decomposition are dropped | **REAL** — `Søren` → `sren`, `Łódź` → `odz` |
| slugify | 24 | Empty-result guard checks the folded text, not the slug | **REAL** — `Москва!` → `''` (the guard sees `!`) |
| retry | 19 | A very large finite delay lets `sleep` mask the real error | **REAL** — `OverflowError` replaces the operation's `KeyError` |
| truncate | 10 | With `width` below the marker length the result exceeds `width` | **REAL** — `width=1` → `'...'`, 3 chars |
| once | 14 & 31 | Docstring says `BaseException`; code catches fewer | **REAL** — `GeneratorExit` propagates twice, so it is not remembered |
| once | 36 | Re-raising the cached instance grows its traceback | **REAL** — 4, 6, 8 frames across three calls |

**False-confirm rate: 0 of 20 = 0%** (0 findings refuted, 7 findings all true positives).

Limits worth stating plainly: one author, one repository, one host, one run. 0% here is consistent with the
previous runs' 17% only in the sense that the seed defects adjudicated in `07183ad` were *fixed* — those
seeds can no longer produce the false confirms they once did. The remaining false-confirm risk is in shapes
the seeds do not contain.

### Duplicate handling

`once` produced the same claim from two lenses, at lines 14 and 31. Both survived — the merge treats
identical normalised titles as distinct when the line differs, and took the line as the only difference.
Three findings on `once` therefore include one duplicated claim:

```
once functx/once.py:14  Docstring says BaseException is caught, but the code only catches Keyb…
once functx/once.py:31  Docstring says BaseException is caught, but the code catches only Keyb…
once functx/once.py:36  Re-raising the same cached exception instance makes its traceback grow
```

**Merged 0, missed 0, duplicated 1.** Not a merge failure — the rule as specified keeps same-claim
different-line findings, and the titles are distinguished only by the appended line.

## Recall guard

`/home/kensei/.hermes/cache/scratch/recall_guard.py`, log `/tmp/recall_guard.log`. Re-reviews the
bug-carrying sandbox PRs through the hosted harness.

| PR | Seeded bug | Result |
|---|---|---|
| #25 | `clamp` ignores its upper bound | **CAUGHT** — `clamp() never applies the high bound, so values above the range come back unchanged` (important), plus `window() misses the last window` |
| #18 | `divide` rejects negative divisors (`b <= 0`) | **CAUGHT** — `Zero check changed to b <= 0, so every negative divisor now raises ZeroDivisionError` (important) |

Both bugs are still reported, with the correct severity, after every change in this round. Fewer false
alarms was not bought with missed bugs.

One caveat on my own guard: it initially scored #18 as MISSED because its needle list did not contain the
words the reviewer used. The reviewer was right and the check was wrong — a recall check whose vocabulary
is narrower than the reviewer's reports false misses. Both PRs were then read by hand to confirm.

## Not done

- macOS/Windows containment of a real fix run — unit tests only on the matrix; GitHub Actions still cannot
  start (billing).
- A hosted CI run ID for `9d240ff` — same billing block; `local-ci` output substitutes.
- `docs/reports/P3B-REPORT.md` still cites amended commit `740b6d4`.
