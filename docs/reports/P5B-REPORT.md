# Phase 5b — TUI and Desktop surfaces

Status: **awaiting Sahil's visual sign-off.**
Commits: `f22eced` (the two Phase 5 fixes), `df17cc8` (Phase 5b surfaces).
Checkpoint CI: **45627544918 — 6/6**. Tests: **918, OK**.

## Done / Not done against the instruction text

The instruction was: two fixes each as a commit with tests, then Phase 5b (both surfaces, wireframes,
throwaway home, one self-contained HTML review file, Done/Not done, stop for sign-off).

### The two fixes

| Requirement | Status |
|---|---|
| Refuse at parse when count exceeds the number of named approvers, stating both numbers | **Done** |
| If approvers could come from an uncountable team, say "cannot be satisfied as written" instead | **Done, as a code comment** — this grammar has no teams or groups, so the count is knowable at parse; the comment records what must change if that ever stops being true |
| Refuse instead of `base_ref or host.default_branch(repo)` | **Done** |
| Read the policy at the PR's base SHA, not the branch name | **Done, with a correction** — see below |
| Test: a PR with no `base_ref` refuses | **Done** |
| Test: the policy fetch uses the SHA | **Done** |

**Fix 1 measured.** `count: 2` with `approvers: [Sahil-SS9]` and `count: 5` with `[a, b]` now refuse at
parse, naming both numbers and what to change. `count: 2` with two approvers still parses, and still
blocks until both approve.

**Fix 2 found a second bug, in the fix itself.** `pr.base.sha` is GitHub's **merge base**, not the tip
of the base branch: the API returned `2e9e11ad` for a branch whose tip was `514cc54b`. Reading the
policy at the merge base applies a *stale* policy — and on PR #25 the tip's policy had already changed
from an unsatisfiable rule to a valid one, so the merge base produced an error about a policy that no
longer existed. The branch is now resolved to its tip once (`host.resolve_ref`), and the policy is read
at that SHA. Verified live: resolved `514cc54b`, fetched the tip's policy.

Worth stating plainly: **the requested change, implemented literally, would have introduced a
correctness bug.** Reading "the SHA" without asking which SHA is the same class of mistake as reading
"the branch".

**The two fixes interacting is worth seeing.** With the stale policy, the helper returns
`state: error` naming the unsatisfiable rule — not "2 more approval(s) needed". After making the policy
satisfiable, it returns "2 more approval(s) needed", which is now *true*. One comment throughout,
updated in place.

### Phase 5b

| Requirement | Status |
|---|---|
| Build both surfaces against the wireframes | **Done** — palette copied exact from `gallery.html` |
| Same CLI/service layer, no logic of their own | **Done** — one `Projection`, two renderers; a test asserts neither surface computes state |
| No merge action anywhere | **Done** — absent from the action vocabulary, asserted by AST over every surface module |
| Run only in a throwaway HERMES_HOME | **Done** — surfaces need no HERMES_HOME at all; they are pure renderers, and nothing was installed |
| ONE self-contained HTML file, images inlined, no external assets, no JS | **Done** — 3.8 MB, 10 inlined screenshots, 0 external refs, 0 script tags |
| All ten states | **Done** — empty, reviewing, findings, INCOMPLETE, approvals blocked, approvals satisfied, observe-only, paused/capped, stop, error |
| Path Sahil can read | **Done** — `/home/kensei/shared/code-shipper-review/phase5b-surfaces.html` |
| Exact commands for Sahil to run the TUI himself | **Done** — below |
| Stop for visual sign-off | **Stopped** |

## How to look at it

The review pack (start here):

```
scp sahil@<rig>:/home/kensei/shared/code-shipper-review/phase5b-surfaces.html .
```

Then open it in a browser. It is one file; nothing else is needed.

To drive the TUI yourself in **your own** SSH session — servers I start are in my network namespace and
you cannot reach them, so run it on your side:

```
cd ~/repos/code-shipper        # or wherever your checkout is
python3 -m code_shipper.cli ship-ui findings
```

Any state: `empty`, `reviewing`, `findings`, `incomplete`, `approvals-blocked`,
`approvals-satisfied`, `observe`, `paused`, `stopped`, `error`.

Resize your terminal to under 72 columns to see the degraded mode, and set `--width 60` to see it
without resizing:

```
python3 -m code_shipper.cli ship-ui findings --width 60
python3 -m code_shipper.cli ship-ui reviewing --height 24
```

To regenerate a Desktop page as a live HTML file you can open directly:

```
python3 -m code_shipper.cli ship-page approvals-satisfied --out /tmp/page.html
```

## The states, and what each is for

| State | Chip | Why it exists |
|---|---|---|
| empty | `NO-PULL-REQUESTS` | The inbox is the landing surface; a blank panel is not legible |
| reviewing | `REVIEWING` | Shows stop and cancel as **two** keys while a run is live |
| findings | `NEEDS-FIX` | The ordinary case: important findings, verdict pinned to the head |
| incomplete | `INCOMPLETE` | A review that did not finish is not a clean bill of health |
| approvals-blocked | `NEEDS-HUMAN` | Names the count, so a reader knows if it is reachable |
| approvals-satisfied | `APPROVED` | Terminal state: one action, open the PR. Merge is visibly disabled |
| observe | `OBSERVE-ONLY` | An observed rule that looked enforced would be worse than either |
| paused | `PAUSED` | Distinct from stopped: waits for a human to raise the cap |
| stopped | `STOPPED` | The last round settled, so nothing is half-pushed |
| error | `ERROR` | The unsatisfiable policy, which used to render as a false claim |

## What the tests pin

35 tests in `tests/test_phase5b.py`. The structural ones:

- **No merge anywhere.** Not in `ALLOWED_ACTIONS`, not offered by any of the 10 states, not referenced
  by any surface module (AST walk), and the Desktop merge control is asserted to be `disabled` *with*
  its reason.
- **The four signals stay four.** A green CI row must not make the approval signal look green; asserted
  directly.
- **Ordering is asserted**, not incidental: error > pause > findings > approvals > clean, and an
  incomplete review outranks a clean one.
- **Staleness is labelled** in both surfaces when the verdict's head differs from the PR's head.
- **Spend is never `$0`** when unmetered.
- **The TUI degrades below 72 columns** instead of clipping actions, and the footer only shows keys
  live in that state (a stopped PR offers no cancel).
- **Desktop HTML is escaped**, needs no JavaScript, and references no external asset.

Writing these found one real bug: a review with **only nits** reported `APPROVE`, which claims there is
nothing to say. It now reports `APPROVE-WITH-ISSUES`.

## Not done

- **A live TUI session bound to real state.** Both surfaces render from fixtures here. There is no
  interactive loop reading keys, and no live data binding — deliberately, since an interactive loop
  would be a fifth place that could hold logic. Wiring it to `poll` output is Phase 6 work.
- **Desktop as a Hermes Desktop plugin surface.** Built as a standalone self-contained page, which is
  what the review file needs. Loading it as a host surface is not attempted.
- **Keyboard, screen-reader and contrast testing.** The wireframe notes list these as following visual
  approval, and they have not been done.
- **The six open questions in `design-notes.md`** (Desktop merge affordance, agent-approval
  confirmation, transport fallback, density, audit retention, verdict naming) are unanswered and need
  your call. I picked a default for one: the Desktop shows a *disabled* merge control with its reason.
- Carried to the final stop: real publish leg, poll as a sustained loop, live mid-loop stop.

## Test output (real)

```
$ ./scripts/run_tests.sh
...
Ran 918 tests in 330.400s

OK
```
