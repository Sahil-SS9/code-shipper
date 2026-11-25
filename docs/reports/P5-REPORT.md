# Phase 5 — `/ship`, CLI surface, Hermes plugin, polling triggers

Status: complete, awaiting review.
Commits: `7cf38be` (the phase), `b4169da` (three defects the real writes found, plus evidence).
Checkpoint CI: **45618689336 — 6/6**. Tests: **867, OK**.

## Done / Not done against the instruction text

The instruction was five items. Item by item.

| Requirement | Status |
|---|---|
| `/ship` command | **Done** — `/ship <owner/repo> <pr> [review\|fix\|watch\|stop\|status\|publish]` |
| The CLI surface completed | **Done** — added `ship` and `poll`; the surface is now doctor, review, watch, stop, ship, poll, config, lessons |
| The Hermes plugin | **Done** — `plugin/code-shipper/`, `hermes plugins validate` passes, `register()` runs in the host's capability probe |
| Installed into a throwaway HERMES_HOME only, never `~/.hermes` | **Done** — 5 host-integration tests with `HERMES_HOME` pointed at a temp dir, plus a test asserting the live path was never created |
| Polling: watch detects a new head each interval and runs the incremental review | **Done** — `steward/poll.py`, wired into `code-shipper poll` |
| Polling: approvals re-evaluation in the same round | **Done** — `_reevaluate_approvals` runs from the same head, so the review and the comment cannot describe different commits |
| Polling: our own wall clock | **Done** — a self-set deadline, plus `--minutes`/`--rounds` |
| Polling: stop/label checks each round | **Done** — checked at the top of the round, *before* the head is read |
| Polling: no webhooks | **Done** — polling only; there is no listener |
| One posted incremental review, read back from GitHub | **Done** — review **6057900427**, COMMENTED, on PR #25 |
| No duplicate on re-run | **Done** — re-run: cached, 0 model calls, still 1 review |
| One approvals status comment created | **Done** — comment id 7162653496 |
| Then updated in place on a push, no second comment | **Done** — same id, `updated_at` later than `created_at`; total issue comments on the PR: 1 |
| T-SCN-05 observe-vs-enforce as a real sandbox scenario | **Done** — same PR, head and policy text except `observe` |
| Not in this stop: TUI and Desktop | **Correctly absent** — Phase 5b, awaiting your visual sign-off |

## The three defects the real runs found

Every one was invisible to the unit tests, and each would have made Phase 5 wrong while green.

**1. The approvals policy was read from the default branch, not the PR's base.** Measured on PR #25:
the policy lived on `demo/base`, and evaluation reported *"the policy has no approval rules"* about a
policy that plainly had a rule. Every PR against a non-default base would be evaluated against a
policy that does not apply to it. This is the same class as the D15d blocker: a security decision made
from the wrong input.

**2. `update_comment` passed `pr=0` to the gate.** The gate refused it as `bad_pr` — correctly. The
caller was wrong, and the fix gives the gate a true PR while the URL still carries the comment id.

**3. `cwd=str(None)` built the string `"None"`.** A reading task has no workdir, and `subprocess`
tried to `chdir` to a directory literally named `None`, so **every lens failed** with
`could not start: No such file or directory: 'None'`. The first review attempt returned zero findings
and `complete: False` — the honest behaviour, which is how it was caught rather than being reported as
a clean PR.

There was also a fourth, smaller one: `cmd_poll` imported `HermesHarness` from a module path that does
not exist (`harness.hermes`; the real one is `harness.adapters`), so that code path had never been
executed.

## T-SCN-05 as a real scenario

The point of the test is that observe and enforce are different *postures*, not different renderings.

| | Observe | Enforce |
|---|---|---|
| Policy text | `count: 2`, `observe: true` | `count: 2`, `observe: false` |
| Result | "Observe only. 0 approval(s) present; 2 would be required." | "2 more approval(s) needed. 0 of 2 present." |
| `state` | `observe` | `pending` |
| Blocks the PR? | No | Yes, in the sense that it is not approved |
| Comments on the PR | 1, updated in place | still 1, updated in place |

Same PR, same head. Only the `observe` flag changed, and the posture changed with it.

## Sandbox evidence (read back from GitHub, not from the create responses)

`docs/reports/evidence/P5/scn05-pr25.json`:

```
pr 25, base demo/base, head 9c2eca0aa5, merged false, state open
reviews: 1 — id 6057900427, COMMENTED, commit 9c2eca0aa5
issue comments: 1
approvals comment: id 7162653496, created 12:38:30Z, updated 12:39:42Z
```

The review carried two inline findings, both the seeded defects:

- `scn05_clamp.py:4` — clamp() never applies the upper bound: values above high are returned unchanged.
- `scn05_clamp.py:9` — window() off-by-one: `range(len(values) - size)` drops the last full window.

The second only existed in the new commit, so the incremental scope genuinely restricted the review to
new work. The re-run of the same head was served from the cache with **0 model calls** and posted
nothing.

## The ship gate requirements

| Gate | Evidence |
|---|---|
| A secret blocks before any write | Test: a plan containing a token raises, no file is written and no branch is created |
| Publish needs a recorded authorisation | Test: without one, `ShipRefused` with code `not_authorised`, no branch |
| A non-TTY run without authorisation fails with a message instead of hanging | **Measured**: exit 2, message names `--i-authorise-publish`, `git rev-list --all` = 0 commits |
| One commit, authored as the configured identity | Test asserts `git log -1 --format='%an <%ae>'` equals the configured identity, and the count increases by exactly 1 |
| `hermes plugins validate` clean | `Validation passed.` — 12 checks including the capability probe |
| `/ship` callable from the host | 5 tests driving `hermes ship` inside a throwaway `HERMES_HOME` |

## Not done / not proven

- **A real `code-shipper ship` run against GitHub.** The flow is tested against a real local git repo
  with a real bare remote and a stub broker; the PR-creation leg was not exercised end to end. I did
  not want to burn the one sandbox publish on a test and the branch protection on `main` refuses
  direct pushes, which is correct behaviour but means the seeding had to go to `demo/base`.
- **`code-shipper poll` as an actual running loop against the sandbox.** The poll *logic* is unit
  tested (12 tests) and the review and approvals halves were each driven directly, but the loop
  itself was not left running for a wall-clock period. Stated plainly rather than implied.
- **A stop arriving mid-loop on a live PR.** The stop path is tested at the unit level (checked before
  the head read, first-round stop does nothing at all) but not observed live.
- **T-ADV-17's container half**, real hosted-provider auth, concurrency: unchanged from earlier stops.
- **`allow_ship` with the plugin**: the plugin refuses `publish` by default and no test enables it,
  because enabling it would mean letting a chat command open a PR. That is deliberate.

## Design choices worth naming

- **The plugin holds no policy.** It calls the CLI, which holds the broker, the scan and the gate. A
  second copy of a rule in a UI layer is how a rule silently stops being enforced.
- **`allow_ship` defaults to false.** A publish capability that is on by default is one you notice
  after someone has used it.
- **The secret scan runs on the exact content that would be committed**, and redaction is applied to
  everything the tool emits — a key in a finding body is leaked just as surely as one in a scan report.
- **`host.approvals()` counts the latest review per author**, not any approval ever given. A reviewer
  who approved and then requested changes has not approved the current head.
- **Polling checks stop before reading the head**, because a stop honoured only between rounds is
  honoured late and one honoured at the end is not honoured at all.

## Test output (real)

```
$ ./scripts/run_tests.sh
...
Ran 867 tests in 328.216s

OK
```
