# Hosted 20-seed run — corrected: it reached 13 seeds, and the rate is 11%

Status: **stopped for review.**

This supersedes `HOSTED-RUN-INCOMPLETE.md`, which under-reported the run. I had read only the third attempt's
log and counted 4 evaluated seeds. The first hosted attempt reached **13 seeds with 9 evaluated**, and its log
was on disk the whole time. Reading the wrong file is not a reason to publish the smaller number.

## The measurement

```
seeds attempted        : 13   (run stopped on the 13th)
seeds evaluated        :  9
seeds excluded         :  4   (lens/verification timeout — took the full budget, no answer)
seeds with ≥1 finding  :  6
seeds with a FALSE one :  1   (clamp)
clean seeds            :  2   (windows, flatten)
findings               :  9   (7 real, 2 false)

false-confirm rate     : 1 / 9 = 11%        (seeds, not findings)
```

Every finding was adjudicated by **executing** the seed code, not by reading it.

| Seed | Findings | Real | False |
|---|---|---|---|
| clamp | 3 | 1 | **2** |
| mean | 1 | 1 | 0 |
| slugify | 1 | 1 | 0 |
| retry | 1 | 1 | 0 |
| humanise | 2 | 2 | 0 |
| dedupe | 1 | 1 | 0 |
| windows, flatten | 0 | — | — |

**7 of 9 findings were real defects.** That is the number that matters most, and it is not flattering: the
reviewer was right seven times about code I wrote, tested, and declared clean.

## The two false confirms, with their text

Both on `clamp`.

**1.** *"New NaN and bounds-order guards ship with no tests in this change."*
False. The seed's own `self_check` calls `clamp(nan, 0, 1)` and `clamp(0, nan, 1)` and asserts both raise,
plus the reversed-range case. The diff the reviewer saw contains only the source file, not the seed's
control — so it reported the absence of something it was never shown. That is a limitation of what the
reviewer is given, not of the reviewer.

**2.** *"Signalling NaN slips past the documented refusal with the wrong exception type."*
False. Measured:

    quiet nan      (0x7FF8000000000000) -> ValueError: value, low and high must not be NaN
    signalling nan (0x7FF0000000000001) -> ValueError: value, low and high must not be NaN

Identical behaviour and identical exception. The reviewer inferred a distinction between NaN classes that
the `value != value` check does not make — and correctly does not, since both are NaN.

## The seven real defects — all in my seed code, all now fixed

**slugify returned `''` for every non-Latin title.** The worst of them.

    'Café'      -> 'cafe'
    'Москва'    -> ''
    '中文标题'   -> ''
    '日本語'     -> ''
    'العربية'   -> ''

NFKD only decomposes scripts that have a decomposition. Cyrillic, CJK and Arabic do not, so
`encode("ascii","ignore")` dropped every character and the title became an empty slug.

**retry let NaN and infinite delays through its guard.** `delay < 0` is `False` for NaN — every comparison
against NaN is False — and False for `inf`. Both reached `sleep()` and surfaced as `Invalid value NaN` /
`timestamp out of range`, masking the operation's exception. This is the same masking bug the guard was
added to prevent last round, in a form the guard could not see.

**humanise printed `'nan TB'`.** Same root cause: `count < 0` is False for NaN, so it passed the negativity
guard and formatted as a size.

**humanise printed `'1024.0 KB'`.** Rollover was tested on the value before dividing, so 1048575 formatted
as "1024.0 KB" rather than "1.0 MB".

**clamp changed the caller's type on an exact tie.** `max(low, min(high, value))` returns the *bound*:
`clamp(3.0, 0, 3)` gave int `3`, not float `3.0`. A caller's type changed because the value happened to
equal a bound.

**mean refused `Decimal` and overflowed on huge ints.** `TypeError: unsupported operand type(s) for +=:
'float' and 'decimal.Decimal'`, and `OverflowError` on `[10**400, 10**400]` — against a docstring promising
"any iterable".

**the `chunk` docstring shipped reviewer-facing prose.** It read: *"Saying 'lists' here was a promise the
implementation never made."* That is me arguing with a previous review, committed into library
documentation. The reviewer flagged it correctly.

All six are fixed in `07183ad`, each with its case added to the seed's own control. `--dry-run` reports **20
clean, 0 discarded**.

## Merged vs missed

**0 duplicates occurred across 9 evaluated seeds, so 0 merged and 0 missed.**

That is an honest zero and a weak one. The seeds are single-function files of 10–20 lines; the #25 shape —
two locations in one function, one root cause — did not arise naturally. The merge rule is unit-tested and
was demonstrated on the #25 shape in an earlier round, but this run produced no natural case to exercise it.

## Limits — read before quoting 11%

1. **9 evaluated, not 20.** Four seeds took the full per-lens budget and returned nothing. If the lenses
   that time out are systematically more likely to over-report, the true rate is higher; if less, lower.
   With 1 of 9, the 95% upper bound is ~52% — the rate is a point estimate, not a precision figure.
2. **One repository, one author, twenty seeds** — all written by me, all Python, all small. A reviewer tuned
   to my idioms says nothing about a stranger's PR.
3. **Standard assertiveness, three lenses.** Other levels are untested here.
4. **The reviewer sees the diff, not the test.** False confirm #1 came from that gap. A real PR would carry
   tests, so the production false-confirm rate on well-formed PRs is plausibly lower.
5. **Hosted model, single vendor.** These results are for the configured `claude-opus-5-5`, not a claim about
   any model.
6. **`clamp` was the worst seed and the only one with false confirms** — one seed out of nine. With n=9, a
   single seed moving in or out changes the rate by 11 points.

## Why the run still stopped at 13

Throughput, not correctness. A lens either answers in 60–150 s or burns its full 330 s budget and fails. At
three lenses plus a verification batch per seed, one unlucky seed costs 20+ minutes and `retry` alone
exceeded 75 minutes on a later attempt.

The runner is **strictly sequential**. The seeds are independent. Four workers puts 20 seeds at ~1.5 hours
instead of 6+, and it is a change to `run_measurement.py` only — not to the reviewer.

That is what I would build next, and it is what would turn 9 evaluated seeds into 20.

## Evidence

- `/tmp/hosted-run.log` — the run this report measures (13 review lines, `exit=143` after the kill).
- `/tmp/hosted-run3.log` — the later attempt that stopped at seed 6.
- Adjudication: every claim above was executed against the seed code in this session; outputs quoted inline.
- `docs/reports/evidence/hosted-run-stopped.log` — the stopped third attempt.
- Commits this round: `f385cce` (process-group timeout), `2d66685` (stdin), `07183ad` (six seeds).

## Suite

1280 tests OK at `9cbaf77`. The seed changes are measurement fixtures, not shipped code, so the suite is
unaffected by `07183ad`.
