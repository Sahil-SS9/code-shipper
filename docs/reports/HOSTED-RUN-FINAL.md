# Hosted 20-seed run — final: 17% false-confirm rate, 12 of 20 evaluated

Status: **stopped for review.**

Supersedes `HOSTED-RUN-INCOMPLETE.md` (which wrongly said no result existed) and `HOSTED-RUN.md` (which
measured 9 seeds from run 1 only). This is the complete picture across both hosted passes.

## Done / Not done

| Item | Status |
|---|---|
| Use the hosted path | **Done** — `hermes chat`, the model a real user runs |
| Run all 20 seeds | **Done** — every seed was attempted |
| Report the false-confirm rate with its limits | **Done** — 17%, limits below |
| Every false confirm with its text | **Done** — all 3, each refuted by execution |
| Merged vs missed | **Done** — 0 / 0, with the caveat that this is a weak zero |
| Seeds excluded, and how many | **Done** — 8 excluded, every one on a lens timeout |

## The measurement

```
seeds attempted         : 20
seeds evaluated         : 12
seeds excluded          :  8   (lens timeout — took the full budget, no answer)
seeds discarded         :  0   (every seed's own control passed)
seeds with ≥1 finding   :  7
seeds with a FALSE one  :  2   (clamp, is_palindrome)
findings                : 10   (7 real, 3 false)

false-confirm rate      : 2 / 12 = 17%
```

Adjudication was by **executing** each claim against the seed, never by reading it.

| Seed | Findings | Real | False |
|---|---|---|---|
| clamp | 3 | 1 | 2 |
| humanise | 2 | 2 | 0 |
| mean | 1 | 1 | 0 |
| slugify | 1 | 1 | 0 |
| retry | 1 | 1 | 0 |
| dedupe | 1 | 1 | 0 |
| is_palindrome | 1 | 0 | 1 |
| windows, flatten, safeget, counter, windowmean | 0 | — | — |

**7 of 10 findings were real defects in code I wrote, tested and called clean.** That is the headline, and
it is not flattering to the code.

## All three false confirms, with their text and the refutation

**1. clamp — "New NaN and bounds-order guards ship with no tests in this change."**

False: the seed's own `self_check` calls `clamp(nan, 0, 1)` and `clamp(0, nan, 1)` and asserts both raise,
plus the reversed-range case. The reviewer is shown the diff, not the seed's control, so it reported the
absence of something it was never given. A limitation of its inputs, not of the reviewer.

**2. clamp — "Signalling NaN slips past the documented refusal with the wrong exception type."**

False. Measured:

    quiet NaN      (0x7FF8000000000000) -> ValueError: value, low and high must not be NaN
    signalling NaN (0x7FF0000000000001) -> ValueError: value, low and high must not be NaN

Identical behaviour, identical exception.

**3. is_palindrome — "`lower()` is not case-insensitive for all Unicode: `casefold()` would be the
caseless comparison."**

The reviewer gave a precise counter-example: *"`'İ'.lower() == 'i̇'` … while `'İ'.casefold() == 'i'`, so
`is_palindrome('İi')` returns False."*

Executed:

    'İ'.lower()    == 'i'  ->  False
    'İ'.casefold() == 'i'  ->  False        <-- the claim is wrong here
    lower()    cleaned: ['i̇', 'i']  -> palindrome? False
    casefold() cleaned: ['i̇', 'i']  -> palindrome? False

`casefold()` on U+0130 produces `i` **plus a combining dot above**, not plain `i`. So `casefold()` gives the
identical result and the proposed fix changes nothing for the example given. The reviewer's general point
— that `casefold()` is the correct idiom for caseless comparison — is true, but its stated counter-example
is false, and a nit whose own demonstration fails is not a confirmed defect.

## The seven real defects — all my code, all fixed in `07183ad`

**slugify returned `''` for every non-Latin title.** The worst.

    'Café'     -> 'cafe'      'Москва'  -> ''      '中文标题' -> ''
    '日本語'    -> ''          'العربية' -> ''

NFKD decomposes no Cyrillic, CJK or Arabic, so `encode('ascii','ignore')` dropped everything.

**retry let NaN and infinite delays through its guard.** `delay < 0` is `False` for NaN — every comparison
against NaN is False — so both reached `sleep()` and masked the operation's exception: the same bug the
guard was added to prevent, in a form it could not see.

**humanise printed `'nan TB'`** — same root cause, `count < 0` is False for NaN.

**humanise printed `'1024.0 KB'`** — rollover tested before dividing; 1048575 formatted as "1024.0 KB".

**clamp changed the caller's type on an exact tie** — `max(low, min(high, value))` returns the *bound*, so
`clamp(3.0, 0, 3)` gave int `3`, not float `3.0`.

**mean refused `Decimal` and overflowed on huge ints** — against an "any iterable" docstring.

**chunk's docstring shipped reviewer-facing prose** — *"Saying 'lists' here was a promise the implementation
never made"* is me arguing with a previous review, in library documentation.

## Merged vs missed

**0 duplicates occurred across 12 evaluated seeds → 0 merged, 0 missed.**

An honest zero and a weak one. These seeds are single files of 160–1700 characters; the #25 shape — two
locations, one root cause — never arose. The merge rule is unit-tested and was demonstrated on the #25
shape in an earlier round, but this run produced no natural case, so this is a statement about the seeds,
not a measurement of the rule.

## The 8 excluded seeds

`percent`, `parsekv`, `truncate`, `once`, `groups`, `iso`, `normalise`, `chunk` — each lost a lens to
`harness timed out` at the 300 s per-lens budget. `truncate` lost two.

These are excluded from the rate, not counted as clean. A run where a lens errored measured nothing, and
counting it as clean is exactly how a broken harness reports perfect accuracy.

**This is the measurement's main weakness.** 8 of 20 lost to a flaky provider path is a big hole. If the
lenses that time out are systematically more likely to over-report, the true rate is above 17%; if less,
below.

## Limits — read before quoting 17%

1. **12 of 20 evaluated.** The 95% confidence interval on 2/12 is roughly 2%–48%. This is a point estimate.
2. **8 seeds lost to timeouts**, and I cannot show they were lost at random.
3. **Both false confirms came from one lens** (`edge`), on 2 of 12 seeds. n is small enough that one seed
   moves the rate 8 points.
4. **One author, one repo, all Python, all small files.** A reviewer tuned to my idioms says nothing about a
   stranger's PR.
5. **`standard` assertiveness, three lenses.** Other levels untested here.
6. **The reviewer sees the diff, not the tests.** False confirm #1 came from that gap; a real PR carries
   tests, so production may be lower.
7. **One model, one vendor** — the configured `claude-opus-5-5`, not a claim about any model.

## Why 8 seeds still timed out, and the real fix

The per-lens budget is 300 s. Concurrency does not help: at 4 workers **every** lens times out (measured
twice, 4/4 UNEVALUATED each time) because four concurrent `hermes chat` calls slow each other past the
budget. At 2 workers some seeds pass. The ceiling is the provider, not the runner.

What would close the gap:

1. **Raise `max_seconds` to 600 for measurement runs** — the cheapest real improvement, since the failures
   are timeouts rather than wrong answers.
2. **Retry a timed-out lens once** — `truncate` lost two lenses and might have finished on a second attempt.
3. **A faster hosted model** for lens calls, if throughput matters more than quality.

All three are measurement-side; none touches the reviewer.

## Evidence

- `docs/reports/evidence/hosted-run-1.log` — run 1, 13 seeds attempted, 9 evaluated.
- `docs/reports/evidence/hosted-run2-remaining.json` — run 2, 11 attempted, 4 evaluated, per-seed errors.
- `scripts/run_measurement_parallel.py` — the concurrent runner, default 2 workers with the reason recorded.
- Commits: `f385cce` (process-group timeout), `2d66685` (stdin), `07183ad` (six seed fixes), `a0637cb`
  (parallel runner).

## Suite

1280 tests OK at `a0637cb`. The seed changes are measurement fixtures, not shipped code.
