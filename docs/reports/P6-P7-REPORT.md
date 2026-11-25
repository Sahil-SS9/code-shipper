# Final back-end stop — Phases 6 and 7

Status: **awaiting review.** D13 is a proposal awaiting a decision; nothing about it is built.
Commits: `686e8c3` (C11, C2, D5), `db05af0` (real runs, broker fix, docs).
Checkpoint CI: **45660646633 — 6/6** (ubuntu, macOS, Windows × py3.11/3.12). Tests: **981, OK**.
`code_shipper/surfaces/` and the review pack were **not touched** in this stop, as instructed.

## Done / Not done against the instruction text

The instruction had six numbered items. Item by item.

### 1. C11 tracked findings — **Done**

| Requirement | Status |
|---|---|
| A write-access user marks a finding tracked via a comment command | **Done** — `@code-shipper track T-<id>`, with `untrack` |
| Each later review checks new commits against it | **Done** — `tracked.check()` |
| Replies when satisfied | **Done** — one reply, and no reply when nothing changed |
| Capped at 20 | **Done** — refused explicitly with the number, not silently dropped |
| Stored as a ledger entry with actor and timestamp | **Done** — plain JSON with an `events` trail |
| Authority from the host API, never the text | **Done** — `authorise(permission=...)`; a test proves a command claiming admin/write access grants nothing |

Two decisions worth naming. A finding counts as satisfied only when it is no longer reported **and** the
file was in this review's scope — absence from a review that never looked at the file is not evidence it
was fixed. And the reply is empty when nothing changed, because a reply on every push makes the
informative replies invisible.

### 2. C2 wrap-up reserve — **Done**

| Requirement | Status |
|---|---|
| Actually hold back a floor | **Done** — ordinary work is refused at `wall_clock - wrap_up` |
| An exhausted run can still post INCOMPLETE and write the ledger | **Done** — `begin_wrap_up()` spends the reserve, one-shot |
| Tested with the main budget at zero | **Done** — `test_the_main_budget_at_zero_can_still_wrap_up` |

Wrap-up spend is also exempt from the main daily cap up to the reserve, so an exhausted run can afford
the message saying it is exhausted. The reserve **scales** (a quarter of the clock, capped at 60s) rather
than being fixed: a fixed 60s default made every run shorter than 60s invalid, which the existing suite
caught. Below four seconds there is no whole second to hold back, so the reserve is 0 and
`reserve_available` reports False — an honest "too short to wrap up" instead of a configuration error.

### 3. D5 guard — **Done**

| Requirement | Status |
|---|---|
| Force-push or rebase → full review | **Done** — `diverged`/`behind` from the compare status |
| Empty incremental diff while the head moved → full review | **Done** |
| Says so in the body | **Done** — `guard_note`, rendered as "**Full review, not incremental.** …" |
| Test both | **Done**, plus `no-baseline` and `unknown-ancestry` |

This also **removed a pre-existing defect**: an empty incremental scope returned `complete=True` with
zero lenses run — the most reassuring possible output about code nothing had examined. The test that
asserted that behaviour now asserts the fallback.

### 4. D13 check-run role — **Proposed, not built**

`docs/reports/D13-PROPOSAL.md`. `write_check_run` remains in `FORBIDDEN_OPS`; nothing in this stop
touches it. The proposal sets out a separate `reporter` role, ten limits each with its test, what I would
not do, and three options. **Stopping for your decision, as instructed.**

### 5. Carried from Phase 5 — **Done, all three**

| Item | Result |
|---|---|
| A real publish leg to GitHub on a sandbox branch, `allow_ship` enabled only inside that test | **Done** — **PR #26**, read back from GitHub |
| Poll as a sustained loop over real wall-clock time | **Done** — 5 rounds, 102.9s, stopped by its own deadline |
| A live mid-loop stop on a real PR | **Done** — a real push detected mid-loop, then stopped |

Details in the "Real runs" section.

### 6. Phase 6 hardening and Phase 7 — **Partly done**

| Requirement | Status |
|---|---|
| README says polling is "checked every N minutes", never "continuous" | **Done** — "once every N seconds", with what that costs |
| The measured false-confirm rate on seeded clean PRs | **Done, with an uncomfortable finding** — see below |
| The publish-readiness checklist | **Done** — `docs/PUBLISH-READINESS.md` |
| Phase 6 hardening (beyond the above) | **Not done as a separate pass** — stated plainly rather than implied |

## The false-confirm measurement, and what it actually showed

Five seeded PRs (#26–#30) reviewed end to end with the real harness. I adjudicated every finding against
the code afterwards, by hand:

| PR | Seed | Findings | Adjudication |
|---|---|---|---|
| 26 | intended clean | 0 | — |
| 27 | intended clean | 0 | — |
| 28 | intended clean | 1 | **False confirm** — demands punctuation/non-ASCII handling beyond the docstring contract |
| 29 | intended clean | 1 | **True positive** — `chunk(items, -1)` returns `[]`; I wrote a real bug |
| 30 | intended clean | 1 | **True positive** — `mean(generator)` raises `TypeError`; I wrote a real bug |

**Two of my four "clean" seeds contained real defects.** Recording that rather than quietly re-seeding,
because it is the most useful thing in the measurement: small, plausible, clean-looking code often is
not, and the reviewer caught both. It also means the intended-vs-adjudicated distinction is essential —
taken at face value the seeds would have read as 3 false confirms in 5 instead of 1 in 3.

**Measured: 1 false confirm in 3 genuinely clean PRs.** All five returned `complete=True`, so none is an
incomplete-review artefact. This is one observation, not a rate, and the checklist says so.

## Real runs, read back from GitHub

`docs/reports/evidence/P6/real-runs.json`:

**Publish leg — PR #26.** The first attempt **failed**, and the failure was the point: `create_pr` passed
`pr=0` to the gate, which refused it as `bad_pr`. The gate was right — opening a PR is not a PR-scoped
operation. Fixed to pass `None`, with a regression test asserting `create_pr` sends no PR number. Then:

```
pr 26, state open, merged false, mergeable_state blocked
head db57b52799a7, base main, files ['publish_leg.py']
author Sahil Saghir <218421507+Sahil-SS9@users.noreply.github.com>
steps plan → scan → authorise → branch → commit → push → pr
```

**Sustained polling.** 5 rounds over 102.9s of real wall clock at a 20s interval, stopped by its own
deadline. Zero reviews — no head moved in that window, which is the correct answer rather than a gap.

**Live push and stop.** A real push during a live loop:

```
[   0.5s] round 1
[  11.0s] round 2
[  21.9s] round 3
[  27.2s] pushed
[  32.3s] NEW HEAD db57b527 -> da45c148
[  32.3s] round 4
stopped: stopped | reviews: 1 | elapsed: 42.3s
```

Detected within one interval, reviewed once, and the stop honoured at the next round boundary.

## What is still open

- **D13** — your decision (see the proposal).
- **Phase 6 hardening as a deliberate pass** — the guards are in, but I did not do a separate hardening
  sweep across the whole system this stop.
- **The false-confirm rate needs real volume.** Three clean PRs is a smoke test.
- **Surfaces** are frozen pending the redesign (stop 5c).
- Carried from before and unchanged: real hosted-provider authentication, macOS/Windows containment of an
  actual fix run, concurrency, PSI isolation.

## Test output (real)

```
$ ./scripts/run_tests.sh
...
Ran 981 tests in 337.285s

OK
```
