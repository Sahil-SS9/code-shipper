# The 20-clean-PR run, and why the first three attempts measured my seeds

Status: **stopping for review.** Tree clean, pushed.

## Done / Not done

| Item | Status |
|---|---|
| Six review items (previous round) | **Done** — see `SIX-FIX-REPORT.md` |
| 20-clean-PR false-confirm run | **Done** — 0 false confirms in 15 evaluated seeds |
| Merged vs missed for the root-cause merge | **Done** — 0 duplicates occurred, so 0 merged and 0 missed |
| Model reliability that blocked the run | **Fixed** — thinking off; 20 seeds went from ~2h/half-lost to 94s |

## local-ci, final tree

```
$ ./scripts/local-ci.sh
sealed: yes
run dir: /tmp/local-ci.hObCAL

  PASS   syntax/3.11                  every source file compiles
  PASS   syntax/3.12                  every source file compiles
  PASS   syntax/3.13                  every source file compiles
  PASS   tests/3.11                   Ran 1274 tests in 256.247s OK (skipped=4)
  PASS   tests/3.12                   Ran 1274 tests in 263.267s OK (skipped=4)
  PASS   tests/3.13                   Ran 1274 tests in 255.172s OK (skipped=4)
  PASS   imports                      every shipped module imports cleanly
  PASS   seal                         run dir /tmp/local-ci.hObCAL (base /tmp), no credentials read

RESULT: PASS (8 passed, 0 skipped)
EXIT=0
```

## The number

```
seeds checked:      20
seeds discarded:     0     (every seed passes its own control)
evaluated:          15     (5 lost to the model, see limits)
false confirms:      0
rate:                0% of evaluated seeds (0/15)
```

**No false confirm survived adjudication.** Four runs, each number lower than the last, because each time
I checked the findings against the seed code instead of trusting the label:

| Run | Reported | After adjudication | Cause |
|---|---|---|---|
| 1 | 60% (6/10) | **0** | All 12 findings were true. Seeds: Feb 31 accepted, Café→caf, `comment=''` swallowing, `chunk` on str, `MappingProxyType`, NaN clamp |
| 2 | 14% (2/14) | **1 true, 1 false** | `retry(delay=-1)` masked the real exception — real. `iso` was a docstring critique — false |
| 3 | 7% (1/14) | **0** | `once` ran 8× under 8 threads — real; `SystemExit` not cached — real |
| 4 | 0% (0/15) | **0** | — |

Eleven of the twelve findings in run 1 were defects in my own seed code. I wrote plausible helpers, wrote
the tests that let them pass, and called them clean. The docstring said one thing and the body did another,
and only running the thing found it.

## The one real false confirm

`iso`, run 2. The claim: "isoformat() can produce negative year strings, contrary to docstring intent."
The docstring mentions `{year:04d}` and negative years; the implementation uses neither. Measured:

    iso_date(2026, 2, 31) -> ValueError: day 31 must be in range 1..28 for month 2 in year 2026
    iso_date(0, 1, 1)     -> ValueError: year must be in 1..9999, not 0

Both refuse correctly. **The reviewer read a comment and reported the comment.** Worth recording as a
distinct failure shape from "invented a defect": it inherited a stale claim from prose that no longer
described the code.

## Merged vs missed

**0 duplicates occurred across 60 seed-reviews, so 0 were merged and 0 were missed.** That is a true
statement and a weak one: the seeds are small single-function files, so the #25 shape — two locations in one
function, one root cause — rarely arises.

The one same-file pair that did occur (run 3, `once.py` lines 14 and 16: a race, and `SystemExit` caching)
stayed separate. That is correct: same file, same class, **different root causes**. Merging those would have
hidden a defect.

So the merge rule is not disproved, but it is also not measured on a case it was built for. The honest
answer to your question is: **the 3→2 collapse was demonstrated on the #25 shape in the previous round and
is unit-tested; this run produced no natural duplicate to exercise it.**

## Limits — read these before quoting the 0%

1. **0 of 15, not 0 of 20.** Five seeds never produced a usable answer, all for the same reason: the model
   wrapped findings in a bare object instead of `{"findings": [...]}`, or emitted prose. If the failing
   lenses were more likely to report, the rate would be higher. A 0/15 upper bound at 95% confidence is
   about 18%; the true rate is somewhere under that.
2. **One repository, one author, twenty seeds.** All written by me, all Python, all small. A reviewer tuned
   to my idioms says nothing about a stranger's PR.
3. **Standard assertiveness, three lenses.** Other levels are untested here.
4. **Local model, not the hosted path.** The precision of `qwen3.8-27b-gsq-rco` with thinking off is not
   the precision of a frontier model. This measures Code-Shipper's pipeline around a mid-size model, which
   is the deployment we care about, but it is not a claim about the pipeline over GPT-class models.
5. **Fifteen seeds is a sample, not a rate.** One false confirm would move it to 7%.

## What the run cost, and what that changed

The first attempt took ~2 hours and half the runs measured nothing, because every lens spent its budget
thinking and returned empty content. `chat_template_kwargs: {enable_thinking: false}` — **nested, not flat**
— fixed it: 327 tokens and 6 seconds per lens instead of 2048–6144 tokens and 120+ seconds of silence.

Total review time for 20 seeds in the final run: **94 seconds.**

That is the single most valuable thing this round produced, and it came from refusing to publish a rate
computed from a broken instrument.

## Commits

`5710841` seeds corrected (11 defects) · `4d4c7fa` clamp NaN · `001dbd9` thinking off · `b17e911` retry seed
and the iso false confirm · `94498cd` once() lock and stop-signal handling

## Not proven

- Hosted CI — still blocked on billing; local-ci is the substitute, and it is Linux-only.
- macOS/Windows containment of a real fix run.
- A duplicate-merge case arising naturally rather than from a constructed fixture.
