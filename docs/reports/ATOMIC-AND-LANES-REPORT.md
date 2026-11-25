# Round — the remaining atomic writes, and the lanes line

Status: **stopping for review.** Commits `500e00e` (atomic writes) and `5f55e8b` (lanes line). Tests
**1156, OK**.

## Done / Not done against the instruction

### The defect: only `review/state.py` wrote atomically — **Done**

| Requirement | Status |
|---|---|
| `review/tracked.py` uses the atomic helper | **Done** |
| `review/memory.py` uses the atomic helper | **Done** |
| One shared function, not copied | **Done** — `code_shipper/atomic.py` |
| The torn-read test applied to both | **Done** — and to `state.py`, so all three agree |
| Cache may stay plain, with the reason in a comment | **Done** |

You were right that this is the same class as the Phase 6 fix, and worse in both places. A torn read is
treated as empty, so the next save writes an empty document over the real one: the tracked ledger loses
which findings were tracked, who asked and when; the memory loses stored lessons. Neither logged a line.

`atomic.write_json_atomic` and `atomic.read_json` now live in one module and all three stores call them. A
test asserts no store reimplements the temp-file-and-rename dance or still calls `write_text`, so a fourth
store cannot quietly diverge.

**The stores differ on purpose in what they do with a failure**, and I want that explicit rather than
uniform:

| Store | On a failed write | Why |
|---|---|---|
| `state.py` | swallows it | losing it means a full review next time, which is safe |
| `tracked.py` | raises | the actor/timestamp trail is the point; an audit that cannot say who asked is not an audit |
| `memory.py` | raises | learned lessons are the whole value of the store |
| `cache.py` | plain write, unchanged | a cache miss costs one model call — that is what a cache is for |

The cache's docstring says that in those words, so the rule is not applied blindly to it later.

**Proven, not asserted.** Each torn-read test runs a writer and a reader in a loop and counts unparseable
reads. Each was run against the old implementation first:

```
tracked ledger, plain write_text  -> FAILS
tracked ledger, atomic            -> 0 torn reads in 2000 attempts
```

### The lanes line clipping at 100 columns — **Done**

At 100 the line was cut by the caller and lost its final step — `○ You …` — the reader's own job. The line
now picks the widest form that fits, in a deliberate order: full step names with both group labels, then
full names with no labels, then shortened names with short labels. If nothing fits, the steps win over the
labels.

| Width | Result |
|---|---|
| 124 | full names, both group labels |
| 100 | full step names, group labels dropped |
| 70 | short names, last step still reads `Merge` |

The test asserts no ellipsis in the lanes line at 80 columns or wider, across all ten states, and that the
final step is visible at 80, 100 and 124. Checked against the old implementation: three failures there,
clean here.

### Commit rule — **Done**

Both commits carry the tests that prove them in the same commit. `500e00e` is the atomic helper plus
`tests/test_phase6_hardening.py`; `5f55e8b` is the lanes line plus `tests/test_phase5c.py`.

### CI — **Not done, and not by me**

| Run | Commit | Result |
|---|---|---|
| `45690342835` | `5f55e8b` | **0/6 — jobs never started** |

Billing has not been fixed on the account. GitHub refused to start any job again:

```
The job was not started because recent account payments have failed or your spending limit needs to be
increased. Please check the 'Billing & plans' section in your settings
```

**So neither commit in this round has CI evidence.** What they have: the full suite run locally against
the exact committed bytes, **1156 tests, OK**, `scope: full`, recorded by the runtime. That is a weaker
claim than a 6/6 matrix and I am not presenting it as equivalent — the py3.11 bug earlier in this project
was found by the matrix and missed by every local run.

**I need you to confirm billing is fixed, then I will re-run CI and report the run ID.** I will not keep
firing runs at a wall while it is broken.

## Test output (real)

```
$ ./scripts/run_tests.sh
Ran 1156 tests in 339.436s

OK
```

## Two of my own tests were wrong first

Both are worth recording because they are the reason the assertions earn their place:

1. The lanes test selected the lanes row by its group label — which silently found nothing in exactly the
   states where the label had been dropped, i.e. the case under test.
2. It then split on `│` to count the icons, but that character also separates the lane groups, so it only
   saw half the row.

A test that cannot find the thing it is testing passes for the wrong reason. Both were caught by the
failure messages, not by review.

## Still unproven

- CI on `500e00e` and `5f55e8b` — billing.
- A real hosted-provider authentication run, macOS/Windows containment of a real fix run, broker
  concurrency under real API load, PSI isolation, and a false-confirm rate at any meaningful sample size.

The sandbox PRs (#25–#30) remain open and unmerged. Nothing is published. `~/.hermes` untouched.
