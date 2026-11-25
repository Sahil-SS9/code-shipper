# Final stop — the six fixes, Phase 6 hardening, and publish readiness

Status: **stopping for review.** Commits `abce61e`, `37cb74a`, `d865555`, `ac69794` (the six fixes, one
surface per commit), `ccfbefa` (Phase 6 hardening). Tests **1141, OK**.

## Done / Not done against the instruction

### 1. Error state copy, and "no findings" in an unfinished review — **Done**

| Requirement | Status |
|---|---|
| Remove "This is a plain cause, not a stack trace." | **Done** — gone from the emitted copy; it survives only in a comment saying why it went |
| A short plain headline, detail below, one Retry | **Done** — "Your approval rule can't be met", cause as the detail, one Retry |
| A state where the review didn't complete must never say "No findings at this head" | **Done** — `findings_placeholder()` decides the wording once |
| Say "The review didn't finish, so nothing was checked." | **Done** — exact wording, for error / reviewing / paused / stopped / incomplete |
| A test across all three surfaces | **Done** — swept across Desktop, TUI wide, TUI narrow and CLI |

One thing the tests forced me to state properly. `incomplete` **has** a finding — the lens that did run
returned one — and it still renders it, under a verdict that says it is not a pass. The placeholder is for
the case where a surface would otherwise print a clean result over an empty list. Suppressing the partial
result would throw away the only thing the review did produce.

### 2. TUI lanes — **Done**

Labels `CODE-SHIPPER` / `YOU AND GITHUB` restored, and kept inside the board width so the right-hand label
cannot be clipped away by the caller. `◐` now means work is happening **now** and nothing else; a waiting
decision is `▸`. In the findings state Fix is `waiting`, asserted directly. Desktop and TUI share one icon
table, asserted equal.

### 3. Wide TUI wraps — **Done**

The Next sub-line and the finding explanation wrap on word boundaries in the wide layout. Only the narrow
layout clips, and a test asserts each: wide has no `…` in the Next bar, narrow does.

### 4. status — **Done**

The reason column uses the row's own short reason ("Review incomplete: not a pass"), never the Next
headline cut mid-word. One age formatter, shared with the board: `1d`, never `24h`, asserted in both.

### 5. Exit codes in `--help` and the README — **Done**

Both, from one constant so they cannot drift. `3` (incomplete) is called out as deliberately distinct from
`2` (error). A test asserts `--help` and the code agree.

### 6. D13 title for `approvals-blocked` — **Done**

`Review clear · approvals still needed (0 of 2)`. Every other state gets an explicit title too. When counts
are not supplied the title still says approvals are needed rather than omitting it. Tests sweep plausible
count pairs and assert every success title in that state names the outstanding approvals, and that no
success title claims approval.

### Phase 6 hardening — **Done, and it found a real defect**

The defect: `ReviewStore.save` used a plain `write_text`, which truncates the file before writing. A
concurrent reader sees a truncated file, `load` falls back to a fresh state, and the next review silently
becomes a full one. No error, no log line — the incremental baseline is just gone. Measured with a writer
and a reader in a loop:

```
plain write_text   -> 61 torn reads (JSONDecodeError) in 6000 attempts
atomic os.replace  ->  0
```

Fixed by writing to a sibling temp file, fsyncing and renaming into place, with the temp file unlinked on
failure. The write stays best-effort: losing it means a full review next time, which is safe.

`tests/test_phase6_hardening.py` (22 tests) covers the failure modes that are quiet rather than loud: torn
writes, twelve concurrent writers, concurrent readers, corrupt / non-object / empty state files, an
incomplete review not advancing the baseline, redirects staying refused, the token never in a repr, and
that a refusal sends nothing.

### Publish-readiness review — **Done**

`docs/PUBLISH-READINESS.md`, updated with this stop's evidence. The honest headline: **the code is close,
the evidence is thin.** Three things stand between this and publishable, and none of them is an engineering
task I can complete alone:

1. **A false-confirm rate that means something.** Currently 1 false confirm in 3 genuinely clean PRs, from
   three clean inputs. That is one observation per PR, not a rate, and the document refuses to quote it as
   one.
2. **Someone else runs the install steps** from the README and gets a working review.
3. **A decision on the approval gate.** Today approval rules only appear in a comment; they do not block a
   merge on GitHub. Whether they should is a product decision, not a bug, and it is the largest open
   question in the design.

## Process notes

- **One surface per commit, as instructed.** `37cb74a` is the TUI, `d865555` is the CLI/status/README,
  `ac69794` is the D13 title, `ccfbefa` is the hardening. `abce61e` still pairs the projection with the
  Desktop, because the projection owns the copy both halves of fix 1 needed and splitting them would have
  left a commit with the rule but no surface using it. That is a smaller breach than before, not an
  absence of one.
- **Three of my own tests were wrong** and were corrected rather than worked around: `has_baseline` is a
  property, `_RefuseRedirects` is the real handler name, and one reader test asserted the state never
  changes, which is nonsense. Two earlier ones in the surface tests assumed a fixture had no findings when
  it has one.
- **Two answers I could not find in the code** and did not guess: whether approval rules should gate a
  merge on GitHub, and what the intended false-confirm threshold is.

## Test output (real)

```
$ ./scripts/run_tests.sh
Ran 1141 tests in 348.372s

OK
```

## Checkpoint CI — one green run, and one that could not start

| Run | Commit | Result |
|---|---|---|
| `45682751422` | `ac69794` (the six fixes) | **6/6** |
| `45684044951` | `ccfbefa` (Phase 6 hardening) | **6/6** |
| `45685682311` | `88b3254` (the surface tests) | **0/6 — jobs never started** |

The third run is not a failure of the code and I am not going to dress it up as one. GitHub refused to
start any job:

```
The job was not started because recent account payments have failed or your spending limit needs to be
increased. Please check the 'Billing & plans' section in your settings
```

An account billing problem, not a test result. **So the last commit has no green CI evidence behind it.**
What it does have: the local suite ran the exact committed bytes — SHA-256 of the working file and of
`git show HEAD:tests/test_phase5c.py` are identical (`fc5809a8…`), tree clean — at **1141 tests, OK**.

Anyone picking this up should re-run CI once the account is sorted before treating `88b3254` as verified.
I am recording the gap rather than letting the local run stand in for a matrix result.

## What is still unproven

Unchanged from before, and stated because a report that lists only what is done is a sales document:

- a real hosted-provider authentication run;
- macOS/Windows containment of an actual fix run (the matrix runs unit tests only);
- concurrency at the broker level under real API load;
- PSI isolation;
- the false-confirm rate at any meaningful sample size.

The sandbox PRs (#25–#30) remain open and unmerged. Nothing is published. `~/.hermes` is untouched and the
plugin has only ever been installed in a throwaway `HERMES_HOME`.
