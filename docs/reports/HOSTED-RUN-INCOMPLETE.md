# Hosted 20-seed run — did not complete. No rate.

Status: **stopped for review.** This is a report of a failure to produce the measurement, not the
measurement. I am not going to dress it up.

## Done / Not done

| Item | Status |
|---|---|
| Switch the run to the hosted path | **Done** — `hermes chat`, the same model a real user runs |
| Run all 20 seeds | **Not done** — the run was stopped at seed 6 after 3h40m |
| False-confirm rate | **Not produced.** No completed run exists |
| Every false confirm with its text | **Partial** — 4 findings from the seeds that did run, adjudicated below |
| Merged vs missed | **Not produced** for this run |

## Why it was stopped

Two real bugs were found and fixed on the way (both committed), then the run itself proved too slow to
finish in a session:

| Seed | Time | Outcome |
|---|---|---|
| clamp | ~15 min | UNEVALUATED — one lens timed out |
| chunk | 404 s | 1 finding |
| mean | 340 s | 1 finding |
| slugify | 224 s | clean |
| retry | **> 75 min, never finished** | stopped |

Projected: 6+ hours for 20 seeds. `retry` alone exceeded the wall-clock available.

The cause is the per-lens timeout interacting with a hosted model that is not deterministic: a lens either
answers in ~60–150 s or takes the full 330 s budget and fails. At three lenses plus a verification batch per
seed, one unlucky seed can cost an hour.

**I stopped rather than report a rate from a partial run**, because a rate over 4 of 20 seeds with 1
unevaluated is not a measurement of anything — it is a number that would need three caveats to be honest.

## Two bugs found and fixed by attempting it

Both were real, both are committed, both would have silently corrupted any future measurement.

**`f385cce` — a harness timeout never ended the call.** `subprocess.run(timeout=)` kills only the direct
child; `hermes chat` is a launcher whose grandchildren inherit the capture pipe, so after the timeout the
parent still waited on the read end. Measured: the run sat at **0% CPU for 25 minutes** on one seed, wedged.
Fixed with `start_new_session=True` and a group SIGKILL. Proved against the exact shape: a launcher spawning
a sleeping grandchild now returns in 5.0 s.

**`2d66685` — the fix dropped stdin.** `Popen` accepts neither `capture_output` nor `input`, and translating
the first hid the second, so every seed failed with `TypeError: unexpected keyword argument 'input'`. What
that run reported was:

```
reviewed 0 clean seeds, 0 discarded
false confirms: 0 across 0 seed(s)
rate: None
```

`rate: None`, not `0%`. The UNEVALUATED guard refused to compute a rate over zero evaluated seeds — the
guard built after the first attempt printed "0 false confirms" while every lens had failed. It held.

## The findings that did appear, adjudicated

Four findings across the seeds that ran. I executed each claim rather than judging it by reading.

### Real — my seeds were defective again

**`chunk`: "Docstring contains a reviewer-facing meta-note."** Correct and embarrassing. The docstring I
added last round reads:

> Slices, not lists: for a str, bytes or tuple input the pieces are of that type. Saying "lists" here was a
> promise the implementation never made.

The second sentence is me arguing with a previous review, shipped into library documentation. The reviewer
was right to flag it.

**`mean`: "Float accumulator rejects Decimal inputs, contradicting the 'any iterable' docstring."** Correct:
`mean([Decimal('1'), Decimal('2')])` → `TypeError: unsupported operand type(s) for +=: 'float' and
'decimal.Decimal'`. The same seed also raises `OverflowError: int too large to convert to float` on
`[10**400, 10**400]`. The docstring promises "any iterable".

**`slugify`: "returns '' for non-Latin titles."** Correct and serious:

    'Café'     -> 'cafe'
    'Москва'   -> ''        <-- a title becomes an empty slug
    '中文标题'  -> ''
    '日本語'    -> ''
    'العربية'  -> ''

NFKD cannot decompose scripts that are not alphabet-based, and `encode('ascii','ignore')` silently drops
what is left. Every non-Latin title collapses to an empty slug.

**`clamp`: "exact-tie inputs return the bound, not the value."** Correct on the type:

    clamp(3.0, 0, 3) -> 3     (int, not float)
    clamp(5.0, 0, 5) -> 5     (int)

Returning the bound rather than the value changes the type, which matters to a caller.

### False

**`clamp`: "signalling NaN slips past the refusal with the wrong exception type."** Not reproduced. Both a
quiet NaN and a signalling NaN (`0x7FF0000000000001`) raise the same `ValueError: value, low and high must
not be NaN`. The reviewer inferred a bug in the NaN check that is not there.

That gives, over the 4 evaluated seeds: **3 seeds with real defects, 1 false confirm** — but 4 seeds is not a
rate and I am not presenting it as one.

## What is needed to finish

The blocker is throughput, not correctness. Options, cheapest first:

1. **Raise the per-lens timeout and run it unattended in the background** across a full session. It will
   take ~6 hours but needs no code change.
2. **Run two lenses instead of three** for the measurement, halving the time and dropping the lens that
   timed out most.
3. **Run the seeds in parallel** — they are independent, and the current runner is strictly sequential.
   This is the real fix: 20 seeds at 4 workers is ~1.5 hours.

Option 3 is the one I would build, and it is a change to `run_clean_measurement.py` only, not to the
reviewer.

## Not produced, and why that matters

- **False-confirm rate**: no completed run.
- **Merged vs missed**: no completed run; no duplicates appeared in the seeds that did run.

## Evidence

- `/tmp/hosted-run3.log` — the stopped run's log (5 review lines, `exit=143`).
- Adjudication transcripts in this session; every claim above was executed, not read.
- Commits: `f385cce`, `2d66685`.

## Suite state

Unchanged by this round's work: 1276 tests, local-ci 8/8 PASS on 3.11/3.12/3.13 at `a4a5255`. The two
harness fixes add 4 tests.
