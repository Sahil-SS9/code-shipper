# STOP B blocker fix + STOP C (Phase 4b) — combined report

Status: complete, awaiting review.
Commits: `d8280ea` (the D15d blocker and a second bug the audit found), `01eb40e` (STOP C / Phase 4b).

## Done / Not done against the instruction text

The instruction was: fix the D15d blocker, re-audit every other field in `merge()` and list each with
its verdict, then continue to STOP C. Item by item.

### The blocker — approval rules were not covered by tighten-only

| Requirement | Status |
|---|---|
| Central rules always apply | **Done** — `rules=tuple(central.rules) + tuple(local.rules)` |
| Local rules are added, never replace | **Done** |
| Can never be weakened | **Done** — and enforced by strictest-wins on the merged set |
| A local rule may not set observe on a central enforced rule | **Done** — and named in `refused` |
| A local approvers list cannot satisfy a central rule | **Done** — and named in `refused` |
| Each loosening named in `refused` | **Done** — three distinct messages |
| Test: repo count 1 → `satisfied=False` + named refusal | **Done** |
| Test: repo approvers [mallory, mallory2] → `satisfied=False` + named refusal | **Done** |
| Test: repo `observe: true` → `satisfied=False` + named refusal | **Done** |
| Test: positive case, local count-3 tightens | **Done** — requires 3, refused empty |
| Re-audit every other field and list each with a verdict | **Done** — table below |

I reproduced all three of your measurements before changing anything, and all three now behave
correctly:

| Case (central: count 2, approvers [alice, bob, carol]) | Before | After |
|---|---|---|
| repo count 1, one approval | `satisfied=True`, `refused=[]` | `satisfied=False`, required 2, refusal naming 1 vs 2 |
| repo approvers [mallory, mallory2] | `satisfied=True`, `refused=[]` | `satisfied=False`, refusal naming mallory |
| repo `observe: true` | `satisfied=True`, `refused=[]` | `satisfied=False`, `observe_only=False`, refusal |
| repo count 3, three approvals | — | `satisfied=True`, `refused=[]` |

### The field-by-field re-audit of merge()

Every field, both directions, probed rather than reasoned about.

| Field | Loosening refused? | Tightening allowed? | Verdict |
|---|---|---|---|
| `approvals` (count) | Yes | Yes — count 3 tightens | **Fixed this round** (was the blocker) |
| `approvals` (approvers) | Yes | Yes — adding approvers is fine | **Fixed this round** |
| `approvals` (observe) | Yes | n/a | **Fixed this round** |
| `approvals` (count 0) | Yes — the strongest case | n/a | **Fixed this round** |
| `allow_agent_approval` | Yes — true→ refused | Yes — false tightens | Was already correct |
| `protected_paths` | Yes — dropping a path refused | Yes — adding a path allowed | Was already correct |
| `budget` | Yes — raising a ceiling refused | Yes — lowering allowed; new keys allowed | Was already correct |
| `scope` — `allowed` | Yes — broadening refused | Yes — narrowing allowed | **Fixed this round** (see below) |
| `scope` — `forbidden` | Yes — dropping a forbid refused | Yes — adding a forbid allowed | Was already correct |
| `agents` | Yes — collapsing to `single` refused | Yes — unrelated keys allowed | Was already correct |
| `trigger` | Yes — re-enabling refused | Yes — interval and other keys allowed | Was already correct |
| `version` | n/a | `max()` of the two | Correct, no security content |

### The second bug the audit found: scope was backwards

`_scope_tightens` used string subsetting. **Narrowing** an allowlist from `src/**` to `src/a.py` was
refused as a loosening — the one case the rule exists to permit. String subsetting cannot compare
globs: `src/a.py` is narrower than `src/**` but is not a subset of it.

Coverage is now decided by asking whether each local pattern is matched by a central glob. Six tests
pin all six directions. The limitation is documented rather than hidden: a local pattern a central glob
would not match textually is treated as a loosening, which fails safe.

This is the same class of bug as the blocker in one respect worth stating: **both were in the direction
of the security test, and both were invisible because no test probed the tightening direction.** The
blocker had tests that asserted the loosening was allowed; scope had tests that only covered
broadening. A tighten-only rule needs a test in each direction per field, which is what now exists.

### Not done

| Item | Status |
|---|---|
| Re-evaluation driven by a real push (polling) | **Not done** — per your decision, polling lands in Phase 5. `watch` has no interval mode that detects a new head and runs incremental review plus approvals re-evaluation. |
| T-SCN-05 as a sandbox scenario | **Not done** — observe vs enforce is unit-tested only |
| T-ADV-17 container half | **Not done** — rests on Phase 0's measurement |
| The status comment posted to a real PR | **Not done** — never posted |
| T-UNIT-POLICY-PARITY (`hermes_yaml`) | **Not applicable** — only the stdlib parser exists |

## STOP C / Phase 4b

| Item | Status |
|---|---|
| Merge risk score (D15a), 5 levels, reasons in the review summary | **Done** — 12 tests |
| Low-effort detection (D15b), default off, one label and one note | **Done** — 10 tests |
| Review memory (D15c), cue/action/evidence, write-access only, off→shadow→on, default shadow | **Done** — 17 tests |
| `lessons list\|show\|withdraw` | **Done** |
| Guardrail: approvals evaluator cannot read the risk score | **Done** — structural, asserted by signature AND by AST over every approvals module |
| Guardrail: detection defaults off, one label and one note, no close or block | **Done** — the actions dict is exhaustive and tested; the module contains no close path |
| Guardrail: non-writer feedback creates no lesson | **Done** |
| Guardrail: shadow changes no output | **Done** |
| Guardrail: withdraw removes the effect | **Done** |
| Guardrail: reversed disposition withdraws automatically | **Done** |
| Guardrail: lessons never touch approvals or security settings | **Done** — AST test over the module's imports |

### Design choices worth naming

- **The risk level is the maximum of its factors, not an average.** One severe signal is not made safer
  by twenty mild ones. An average would let a large boring diff mask a migration.
- **The risk guardrail is structural, not advisory.** `evaluate()` takes `(policy, facts)` and `Facts`
  has no risk field. "The score is only advisory" is worth nothing if the policy engine can consult it.
- **Detection computes no signals at all when off.** Not filtered later — never computed, so it cannot
  leak into a log as a judgement about a contributor.
- **Memory is a plain JSON file under the state dir.** Harness-neutral and memory-provider-neutral by
  construction, which is what D15c asked for.
- **A non-writer's feedback is reported as ignored, not dropped.** A contributor whose feedback vanishes
  cannot distinguish "not accepted" from "broken" — the same reasoning as command handling in P3-C.

## Test output (real)

```
$ ./scripts/run_tests.sh
...
Ran 785 tests in 120.541s

OK
```

Checkpoint CI **45578750911 — 6/6** across ubuntu, macOS and Windows × py3.11/3.12.

## Correction to the previous report

Your C12 correction stands and I have noted it here as you asked: the per-PR cap and stated pause
existed at `f6f5942`, and my pushback was right because the search output was truncated before reaching
those lines. The previous report's claim that C12 was missing was the reviewer's, not mine, and it has
been withdrawn.
