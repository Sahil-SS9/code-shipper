# Phase 5c report — the approved redesign

Status: **awaiting Sahil's visual sign-off.** Commits `73c2d7c` (D13), `3de76a4` (the redesign),
`f25e903` (a CI-found fix). Tests **1086, OK**.

## Done / Not done against the instruction

### D13 — check-run role, option 1 with your correction — **Done** (`73c2d7c`)

Your correction changed the design, and it was right. On GitHub a `neutral` conclusion **satisfies a
required check**, so it is not a neutral choice for a review tool — it is a pass wearing a different word.
Proposal limits 6 and 8 would have let an incomplete review and an observe-only review pass a gate.

| Requirement | Status |
|---|---|
| An incomplete review maps to `action_required`, never `neutral` or `success` | **Done** |
| Observe mode writes no check at all | **Done** |
| A test that no non-complete state produces `success` or `neutral` | **Done** — an exhaustive sweep over every state × incomplete |
| All ten limits otherwise kept | **Done** |
| Off by default | **Done** — `write_check` is reachable only by explicitly passing `role="reporter"` |
| Its own commit with `tests/test_check_role.py` | **Done** — `73c2d7c`, 39 tests |
| Included in this report | **Done** — below |

`ALLOWED_CONCLUSIONS` is `{success, failure, action_required}`. `neutral` is absent by construction, not
discouraged by a comment.

The mapping, and why each line:

| State | Conclusion | Reason |
|---|---|---|
| complete review, findings | `failure` | something to fix |
| complete review, no findings | `success` | genuinely clean |
| incomplete | `action_required` | someone must look; not a pass, not a verdict |
| observe | **no check** | a passing check for a rule that is not enforced marks a gate green for nobody |
| error | `failure` | the review did not happen |
| paused / stopped / reviewing | `action_required` | in flight or interrupted; not a pass |

Three sweeps guard it. One asserts every state × incomplete yields `action_required` or no check, and
that it collected **zero** violations rather than merely not finding one. One asserts no complete state
yields `neutral`. One asserts every projection state has an explicit mapping, so a new state cannot
inherit a green default — an unknown state is refused as `unknown_state` rather than guessed.

The role is separate, as proposed: `reporter` is the only holder of `write_check_run`, asserted by a test
that enumerates holders. Reviewer and approver cannot write a check, and the refusal sends nothing
(outbound count zero). The four destructive operations stay in `FORBIDDEN_OPS`.

### Phase 5c — **Done**, awaiting sign-off

| Requirement | Status |
|---|---|
| Built from the three approved files | **Done** — tokens copied, not imported |
| One projection, no logic in the surfaces | **Done** — asserted by AST and by a copy-detection test |
| No merge or approve anywhere | **Done** — extended to the palette and the CLI, as asked |
| Small commits | **Partly — see below.** D13 and 5c are separate; 5c is one large commit |
| Tests first | **Done** |
| A review pack: all 10 states × Desktop, wide TUI, narrow TUI, CLI | **Done** |
| TUI commands with absolute paths for Sahil's SSH session | **Done** — below |
| Stop for visual sign-off | **Stopping** |

## The commit-size note

You flagged `686e8c3` bundling C11, C2 and D5 into one 1,159-line commit. I kept D13 separate this time:
`73c2d7c` is D13 alone, 505 lines.

**`3de76a4` is one 2,600-line commit for all of Phase 5c, and that breaks the rule again.** The honest
reason: the three surfaces and the projection are a single coherent change — splitting them would have
meant committing TUI code that renders verdicts the projection did not yet produce, so every intermediate
commit would have been broken. I could have landed the projection and fixtures first, then desktop, then
TUI, then CLI. I did not, and it is the same mistake in a new place. From here: one surface per commit.

## Structural claims, and how they are tested

| Claim | Test |
|---|---|
| No surface contains the Next-bar copy | Every approved headline asserted absent from every surface module's source. **This found a real one**: the Desktop empty state hardcoded its heading. Fixed in the code. |
| No merge/approve in the palette | `palette.assert_safe()` plus explicit sweeps over entries and labels |
| No merge/approve in the CLI | The subcommand names, enumerated from the live parser |
| No merge/approve in the TUI | Both key maps — the static keys and the per-state action row |
| No merge/approve control in the Desktop | No disabled Merge/Approve text, no CSS class, no AST attribute call |
| Every state renders in all four surfaces | 10 × 4 |
| Every TUI line is exactly `width` | 10 states × 3 widths, every line |
| Exit codes | 0/1/2/3 asserted per state, and incomplete is distinct from error |
| Colour off without a TTY, off under `NO_COLOR` | Both directions |

## Two real bugs found while wiring, both fixed in code

**The palette offered a `fix` action with no `fix` verb.** A palette entry that cannot run is a lie
about what the tool does. A `fix` subcommand now exists with a working `--dry-run`; a live round defers
to `watch`, which owns the round, the budget and the stop signal, rather than duplicating them.

**The TUI key bar listed `s stop` on an already-stopped pull request.** A key bar that advertises dead
keys teaches a user to distrust every key on it. The action row is now derived from
`projection.actions()`, so it only ever shows live keys.

## What the 5b tests said, and what I did about it

Twelve 5b tests failed, all asserting the superseded design: text chips like `NEEDS-FIX`, a three-column
page, disabled Merge/Approve buttons, a 113-column board, the old palette. I **rewrote** them to the
approved design rather than deleting them, because the parts of 5b that were right still matter and are
still asserted: no merge action, one projection, an incomplete review is never a pass, stale verdicts are
labelled, and the board degrades rather than clipping.

One of those rewrites is worth flagging. 5b showed *disabled* Merge and Approve buttons with their
reasons, on the wireframes' advice. The approved redesign removes them and states the boundary in words
instead — "You merge", and the footer. Both designs refuse the action; they disagree about whether an
unpressable button teaches or teases. The test now asserts the approved choice, and says why.

## Running the TUI yourself

Absolute paths, in your own SSH session:

```
cd ~/repos/code-shipper

# The board, every state
python3 -m code_shipper.cli ui findings
python3 -m code_shipper.cli ui reviewing --height 24
python3 -m code_shipper.cli ui empty
python3 -m code_shipper.cli ui incomplete
python3 -m code_shipper.cli ui approvals-blocked
python3 -m code_shipper.cli ui approvals-satisfied
python3 -m code_shipper.cli ui observe
python3 -m code_shipper.cli ui paused
python3 -m code_shipper.cli ui stopped
python3 -m code_shipper.cli ui error

# Narrow: collapses to one column with ← list
python3 -m code_shipper.cli ui findings --width 60

# The list, with its interval
python3 -m code_shipper.cli status --repo Sahil-SS9/code-shipper-sandbox

# The Desktop page for one state, as a file you can open
python3 -m code_shipper.cli ship-page findings --out /tmp/findings.html
```

The review pack, one self-contained file, 3.9 MB, 10 inlined screenshots, no JS, no external assets:

```
scp sahil@<rig>:~/shared/code-shipper-review/phase5c-surfaces.html .
```

## Not done

- **Sahil's visual sign-off** — yours to give, not mine to assume.
- **Phase 6 hardening** — held for the final review stop, per your instruction.
- **The 5c commit is one large commit** — noted above, with the correction for next time.
- Carried and unchanged: a real hosted-provider auth run, macOS/Windows containment of an actual fix run,
  concurrency, PSI isolation. The false-confirm figure is still 1-in-3 from three clean PRs.

## The CI matrix caught a real bug, and that is the most useful thing in this stop

`45670855049` came back **3/6**: all three py3.11 jobs red, all three py3.12 jobs green. One line in
`cli_view.py` nested an f-string inside another f-string's replacement field, reusing the same quote
character:

```python
f"{paint(f'· head {projection.head[:8] or "—"} · ...', 'dim')}"
```

PEP 701 legalised that in 3.12. On 3.11 the inner quote terminates the outer f-string, so the remainder is
lexed as code — which is why the error read `invalid character '—' (U+2014)` at the position of an em dash
that was inside what looked like a string. Fixed by building the string in a step of its own.

**Two guards for it were written and thrown away**, and that is the part worth recording:

| Attempt | Verdict |
|---|---|
| `ast.parse(..., feature_version=(3, 11))` | **Useless.** Run against the actual broken file, it accepted it. The tokenizer rejects this before `ast` is reached. It would have sat in the suite catching nothing. |
| Regex for "reused quote inside an f-string" | **Useless.** Matched ~30 innocent lines per file. |
| Compile every file with a real 3.11 interpreter | **Works.** Verified against the pre-fix file from git history: reports line 107, `invalid character '—'`, exactly as CI did. |

It also found a stale untracked `build/lib/` copy of the old file, now removed.

The lesson is narrower than "test on 3.11": it is that the first two guards *looked* like protection. I
only found out they were inert by feeding them the known-bad file. A guard that has never been shown to
fail on the bug it exists for is not yet a guard.

## Test output (real)

```
$ ./scripts/run_tests.sh
Ran 1086 tests in 336.069s

OK
```

**One honest caveat on that number.** An earlier run of the same suite reported 2 failures, both in
`test_phase5_host`, both from this message:

```
hermes: source-update completion failed: an update is still running;
wait for it to exit, then relaunch Hermes
```

An unrelated `hermes update` was holding a lock while the suite ran. Those two tests shell out to the
real `hermes` binary, so they fail whenever it is mid-update — the code under test was not involved, and
the clean re-run after the update finished confirms it. Worth knowing: the suite is not hermetic against
a concurrent Hermes update, and that is a real (small) fragility rather than a flake to shrug at.

## Checkpoint CI

| Run | Result |
|---|---|
| `45670855049` (on `3de76a4`) | **3/6** — all three py3.11 jobs red, all three py3.12 green |
| `45674080586` (on `f25e903`, after the fix) | **6/6** — ubuntu, macOS, Windows × py3.11 and py3.12 |

The first run is included deliberately: it is the one that found the bug, and a report that listed only
the green run would be describing a different stop.
