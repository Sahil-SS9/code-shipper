# STOP A — Incremental review and the wiring

Status: complete, awaiting review.
Commits: `0ae2ddf` (incremental review, filters and cache wired, profile, coverage, cap, CI hints) + `f25be8c` (the union-rule defect the real run found)

Nothing merged or approved. Every write path is unchanged from STOP-verified P3.

## C3 — incremental review

| Rule | Implementation | Test |
|---|---|---|
| On-push / on-open covers only commits since `last_reviewed_head` | `restrict()` filters the full diff to files the since-diff touched | `test_an_incremental_review_covers_only_new_files` |
| A manual review covers the whole PR | trigger is passed through; only on-push/open scope down | `test_a_manual_review_covers_the_whole_pr` |
| Only a **complete** review moves the baseline | `advance()` returns unchanged when `complete=False` | `test_an_incomplete_review_does_not_advance_the_baseline` |
| Never moved by a cache hit | cache returns before `advance` is reached | `test_the_same_head_twice_makes_zero_model_calls_the_second_time` |
| `important_open` is a union over the whole PR | `open_important` accumulates, and only a pass that **covered** a file may clear its findings | `test_an_unfixed_important_finding_survives_a_pass_that_did_not_cover_it` |

Scoping is a **filter**, not a subtraction: a file still in scope is shown in full, because a hunk
rebased onto the last review loses the context a reviewer needs to judge it.

## The two end-to-end tests the review asked for

Both run through the real `review` service with a counting harness, so "zero model calls" is measured:

| Test | Result |
|---|---|
| Same head twice | 1st: `cached=False`, calls > 0. 2nd: `cached=True`, **0 calls** |
| Lockfile-only change | `package-lock.json` absent from `coverage.reviewed`, named in the ignored summary, review still runs on the source file |

## C1 and C20 are wired, not just built

This was the outstanding item from the P2-C review: components that were tested but never called.
`cmd_review` now goes through `review/service.py`, which applies the path filter, checks the cache,
scopes an incremental pass, runs the lenses, applies the profile, and advances state — in that order.

## Price of the cache, measured

Second review at the same head on PR #19: **3.07s wall clock, `model_calls: 0`**. The first was a full
model run.

## The real incremental run — sandbox PR #19

| Step | Result |
|---|---|
| Review 1 (full) | head `ad06af7`, `incremental: false`, 1 finding |
| Second commit | `a6fb970` adds only `calc/limits.py` |
| Review 2 (on-push) | `incremental: true`, scope `included: [calc/limits.py]`, `excluded: [calc/__init__.py]`, since `ad06af7` |
| Finding in the new file | "[important] calc/limits.py:3 clamp ignores the high bound and can return values above high" |
| Duplicate check | re-review at the same head: `cached: true`, `model_calls: 0`, no new comment |

The finding is correct: `clamp()` returns `max(low, value)` and never uses `high`, so
`clamp(10, 0, 5)` returns 10.

## The defect the real run found

The first run of that sequence reported the finding but left `open_important` **empty**. Findings reach
`advance()` without a fingerprint — identity is normally computed at posting time — so the union rule
keyed on nothing, and the one mechanism that keeps an important finding alive across pushes was blind
to it.

`advance()` now derives the fingerprint when absent. Nine unit tests had passed with a pre-stamped
finding; only the real run exposed that the pipeline does not stamp one.

## C5, C9, C12, C10

- **C5 profile**: quiet hides nits and *counts* them; standard keeps them; assertive is a lens-level
  matter. No profile changes whether verification runs — that is depth's job (D10).
- **C9 coverage**: files reviewed, with findings, clean, ignored. "Clean" is stated explicitly, because
  that is what separates reviewed-and-clean from never-opened.
- **C12 cap**: pauses before any model call, with a stated reason; a paused PR stays paused and costs
  nothing.
- **C10 CI annotations**: extracted as paths and delivered inside an untrusted block, labelled "a hint
  about where to look, not a conclusion … it can be wrong or misleading".

## Not proven

- **Posting** an incremental review to GitHub. Everything here is `--dry-run`; the posting path is the
  P2-B broker path, unchanged, and an incremental body has not been posted.
- **A real on-push trigger** driven by a webhook or poller. `--trigger on-push` is passed on the
  command line; nothing yet fires it from a push event.
- **The cache across process restarts in anger** — it round-trips through disk and is tested, but the
  run above was one process.
- **`assertive` does anything at the lens level.** It is a recognised profile that changes no output
  yet; "lowers the bar for Important" needs a lens-level threshold that has not been built.

## Reproduce

```
cd /home/kensei/repos/code-shipper
./scripts/run_tests.sh                                        # 651 tests

# the real incremental sequence, on the sandbox
rm -rf /tmp/s && python3 -m code_shipper.cli review 19 --repo Sahil-SS9/code-shipper-sandbox \
  --level quick --dry-run --state-dir /tmp/s --json
python3 -m code_shipper.cli review 19 --repo Sahil-SS9/code-shipper-sandbox \
  --level quick --dry-run --state-dir /tmp/s --trigger on-push --json
```

## Test output (real)

```
$ ./scripts/run_tests.sh
...
Ran 651 tests in 121.271s

OK
```
