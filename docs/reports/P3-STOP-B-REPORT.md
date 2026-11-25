# STOP A conditions + STOP B — approvals engine

Status: complete, awaiting review.
Commits: `2f26232` (STOP A conditions), `6978bbc` (STOP B).

## Done / not done against the instruction text

The instruction for this stop was: three STOP A conditions, then STOP B. Item by item.

### Condition 1 — `covered_paths` required

| Requirement | Status |
|---|---|
| Required keyword, no default | **Done** — `def advance(*, ..., covered_paths: list[str], ...)` |
| `None` refused | **Done** — explicit `TypeError` before any mutation |
| `[]` means "cleared nothing" | **Done** — and tested |
| Signature test | **Done** — `test_the_signature_has_no_default` asserts `Parameter.empty` and `KEYWORD_ONLY` |
| Omitted-is-TypeError test | **Done** — `test_omitting_it_is_a_type_error` |
| Your measurement reproduced | **Done** — `test_an_omitted_pass_cannot_clear_an_important_finding`, using the real `Finding` class |

### Condition 2 — C12 and C10

| Requirement | Status |
|---|---|
| C12 per-PR review cap with a stated pause | **Already built in STOP A**; I checked before changing anything. `service.run` pauses at the cap before any model call, saves `paused`/`pause_reason`, and returns without calling the model. Two tests pass: `test_the_cap_pauses_with_a_stated_reason`, `test_a_paused_pr_stays_paused_without_calling_the_model`. `--max-reviews` is wired into the CLI. **I disagree with the finding that it was missing** — and I am flagging that rather than silently agreeing, because a report that agrees with everything is not a check. |
| C10 CI annotations as untrusted hints | **Done, and it was genuinely missing.** `log_tail` was accepted by the service and never passed by the CLI. Now: `GitHubHost.failing_log()` and `annotations()`, `review --with-ci` fetches both, annotation paths wrapped in `hint_block()`, log passed as an untrusted block. 5 tests. |
| Next report opens with Done/Not done | **This document.** |

### Condition 3 — `assertive`

| Requirement | Status |
|---|---|
| Implement the lens-level threshold, or refuse `assertive` | **Implemented.** `lens_prompt(..., assertiveness=)` now asks for a low bar (include what a careful reviewer would raise even when unsure, and say so), `quiet` for a high bar, `standard` in between. Threaded through `review()` and the service. 6 tests, including that the three prompts differ and that none weakens the untrusted framing. |

### STOP B — approvals engine

| Item | Status |
|---|---|
| Policy parser | **Done** — `approvals/policy.py`, closed grammar, stdlib only |
| Schema / validation | **Done** — 20 grammar tests, mostly refusals |
| Facts | **Done** — `Facts`, with `previous_paths` so a rename counts under both paths |
| Evaluator | **Done** — `approvals/evaluate.py` |
| Status comment | **Done** — one comment, edited in place, skipped when unchanged |
| Re-evaluation on push | **Partial** — the evaluation and comment rendering are pure functions keyed on the head; nothing yet *triggers* them from a push event. Same gap as STOP A's on-push trigger. |
| T-UNIT-POLICY-01..20 | **Done** (20 grammar tests) |
| T-UNIT-EVAL-01..15 | **Done** (16 evaluation tests) |
| T-ADV-01 | **Done** — a policy read from the PR while the PR edits it is refused |
| T-ADV-16 | **Done** — `allow_agent_approval` requires `agents.mode: separated`, stated explicitly |
| T-ADV-17 | **Partial** — the identity half is done and tested; the container half (a fix run cannot read `~/.config/gh`, the keyring, `.env`, or reach the Docker socket) was measured in Phase 0 and is not re-run here |
| T-SCN-05 | **Not done** — observe-vs-enforce is unit-tested, not exercised as a scenario on the sandbox |
| C13 config preview | **Done** — `code-shipper config effective --repo o/r --central … --local …` |
| D15d tighten-only | **Done** — 11 tests; every loosening refused with the key named |

## Three defects the tests found

All three were in the parser I had just written, and all three were found by writing the refusal test
first rather than after:

1. **Quoted list items kept their quotes.** `protected_paths: ["**/secrets/**"]` parsed as a pattern
   with literal quote characters, so it matched nothing — a protected path silently not protected.
2. **Anchors were silently accepted.** `approvals: &anchor` parsed as an empty value, dropping the
   rules attached to it. Now refused.
3. **A missing `agents.mode` defaulted into permission.** Only `mode == "single"` was refused, so
   `allow_agent_approval: true` with no `agents` block at all was accepted. Now `mode` must be
   explicitly `separated`.

## `config effective` output (real)

```
$ python3 -m code_shipper.cli config effective --repo o/r \
    --central /tmp/central.yml --local /tmp/local.yml
key                     value                     source
----------------------  ------------------------  ----------------------
allow_agent_approval    False                     central
protected_paths         2 pattern(s)              central + repo (widened)
approvals               0 rule(s)                 central
budget                  1 setting(s)              central (refused)
...
refused (a repo may tighten but never loosen):
  - protected_paths: the repo dropped **/secrets/** from the protected list, which
    loosens a security key; keeping them
  - budget: the repo's value loosens the central setting, which is not allowed;
    keeping the central value
```

## Not done / not proven

- **Re-evaluation driven by a real push.** The evaluator and comment are pure functions of the head;
  no webhook or poller calls them. This is the third stop where a trigger is the missing piece.
- **T-SCN-05 as a scenario** on the sandbox: observe mode and enforce mode against a live PR.
- **T-ADV-17's container half** is not re-measured; it rests on Phase 0's finding.
- **The status comment has never been posted** to a real PR.
- **`hermes_yaml` parity** (T-UNIT-POLICY-PARITY) is not applicable yet: only the stdlib parser exists.

## Test output (real)

```
$ ./scripts/run_tests.sh
...
Ran 732 tests in 118.136s

OK
```

Checkpoint CI **45573074497 — 6/6**.
