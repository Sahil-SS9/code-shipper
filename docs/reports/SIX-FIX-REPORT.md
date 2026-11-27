# Round: six items from review, and what the measurement run is actually telling me

Status: **stopping for review.** Commits `633d16b`, `255307b`, `08753b6`, `9655cb3`, `5249024`, `ce1b064`
(plus `fa0d05c`, the adapter fix the measurement needed).

## Done / Not done

| # | Item | Status |
|---|---|---|
| 1 | `test_p2c` still expects "Also noted on this line:" | **Done** — `633d16b`; and the wrong-commit slip is addressed below |
| 2 | `cmd_review` exit codes wrong | **Done** — `255307b` |
| 3 | `--pr` abbreviates to `--profile` | **Done** — `08753b6` |
| 4 | Ctrl+K palette drawn permanently open | **Done** — `9655cb3` |
| 5 | `--demo` banner breaks the grid | **Done** — `5249024` |
| 6 | Hermes tests fail on a missing `ruamel` | **Done** — `ce1b064`, now an honest skip |
| — | 20-clean-PR run | **Not done — running, and the infrastructure is failing.** See below |

## Evidence: full local-ci, all gates

```
$ ./scripts/local-ci.sh
local-ci  ·  repo ~/repos/code-shipper
sealed: yes
run dir: /tmp/local-ci.r34qqx

gates
-----
  PASS   syntax/3.11                  every source file compiles
  PASS   syntax/3.12                  every source file compiles
  PASS   syntax/3.13                  every source file compiles
  PASS   tests/3.11                   Ran 1274 tests in 263.386s OK (skipped=4)
  PASS   tests/3.12                   Ran 1274 tests in 263.785s OK (skipped=4)
  PASS   tests/3.13                   Ran 1274 tests in 274.306s OK (skipped=4)
  PASS   imports                      every shipped module imports cleanly
  PASS   seal                         run dir /tmp/local-ci.r34qqx (base /tmp), no credentials read

RESULT: PASS (8 passed, 0 skipped)
EXIT=0
```

1274 tests on each of 3.11, 3.12 and 3.13. The 4 skips are the Hermes-install tests, each naming its own
reason.

## Item 1, and the wrong-commit pattern

The test is fixed. The commit ordering is not, and cannot be made right without a force-push, which is not
allowed: `git log -S "class AlsoNotedTests"` shows the tests landed in `6824b04` while the behaviour they
describe changed in `9d9dbaf`, so `6824b04` alone fails.

That is the fifth time. The cause is consistent and it is mine: I stage named files, which means I stage
what I remembered rather than what I changed. What I am doing differently, starting now — and did for all
five commits in this round:

```
git status --short        # read it before staging
git add <every listed path>
git status --short        # must be empty after the commit
```

Every commit above was built by extracting one fix into the tree, verifying only that fix's tests pass,
committing, and confirming the tree was clean before the next. That is why seven commits came out with
exactly the files each one needed.

## The measurement: not a result yet, and why

The 20-clean-PR run is on seed 10 of 20. It is not producing a trustworthy rate, and the reason is not the
reviewer:

```
reviewed so far: 10 seeds
unevaluated:     5 seeds (lens errors, not counted)
clean:           3 seeds
false confirms:  2 seeds
```

**Five of ten runs measured nothing.** The local model returns an empty answer often enough that lenses
fail even at 6,144 tokens with one retry — `no text in the API response after retries`. A rate computed
from the surviving half would describe the model's reliability, not the reviewer's precision.

So I am not reporting a rate. What I can report as observations:

- `clamp` — 1 important: "NaN inputs are not handled and can be silently converted to a bound". The seed
  is clean by its own control (`clamp(10, 0, 5) == 5`, reversed range raises). This is a false confirm:
  the reviewer raised a real-sounding robustness point about code that behaves correctly for the contract
  the docstring states.
- `chunk` — 1 nit: "chunk() may yield non-list slices despite docstring saying lists". Also a false confirm
  by the same test, and a weaker one: the docstring says "lists" and `items[start:start+size]` on a list is
  a list.

Both are the kind of finding that is defensible in review and wrong as a claim about a defect — which is
the failure mode that matters, and matches the earlier 1-in-3 observation.

**To finish this properly** the run needs a model that answers reliably. Options, in the order I would try
them: a non-reasoning model for the lens calls; a higher retry count with a shorter per-call prompt; or the
hosted path. I am not going to keep spending two hours per ten seeds to get a coin-flip completion rate.

## What changed in the adapter, and why it belongs to this run

`fa0d05c`. Every lens was returning `finish_reason: length` with empty `content`, because a local reasoning
model spent the whole budget thinking. Measured 5,279 completion tokens for a 1.2 KB lens prompt. Default
budget is now 6,144, timeout 600s, one retry, and an unanswered lens fails honestly rather than being
recorded as "no findings".

That last part is the one I would defend hardest: the first version of the measurement tool printed "0
false confirms" while every lens had failed. A broken harness reporting perfect accuracy is worse than no
measurement.

## Still unproven

- The false-confirm rate, and merged-vs-missed for the root-cause merge — blocked on model reliability.
- CI on any commit — the account still cannot run Actions. local-ci stands in, and it is honest about its
  own reach: same gates, same three versions, Linux only.
- A real contained fix run on macOS or Windows.
