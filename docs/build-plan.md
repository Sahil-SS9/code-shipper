# Code-Shipper — Build Plan

Status: owner approved the CLI, TUI and Desktop wireframes on 24/11/2026 (R3, `docs/ux/`), and
`docs/HANDOFF.md` is the pinned build handoff (R4). Phase 0 is authorised; each later phase still
needs a GO after its report.

Rules for every phase:
- Do ONE phase, then stop and write `docs/reports/M<n>-REPORT.md` (template at the end).
  The next phase waits for the owner's or KENSEI's GO.
- Each gate item is labelled VERIFIED / PARTIAL / NOT DONE with an evidence path.
- Evidence (logs, command output) goes under `docs/reports/evidence/M<n>/`, redacted. No tokens,
  no real user history.
- Long-run acceptance is capped at one hour per phase.
- No remote push, repo creation, catalog submission or live Hermes install/enable
  without the owner's explicit go (see `docs/HANDOFF.md`).

## Pre-build gates (before any phase below)

- R0 Document reconciliation — done with this revision: REVISION-2 is folded into the canonical
  docs. Still owed: the validated parity feature matrix and a written policy grammar test set
  (tech spec §4.1 states the grammar; the matrix is not written yet).
- R1 Contained worker plus host inference proof, broker authority and expected-head tests with
  negative controls — this is Phase 0 below. No real secret mounts, no global security change.
- R2 TUI widget and Desktop backend transport feasibility, isolated, with no live plugin install.
- R3 CLI, TUI and Desktop wireframes plus the parity decision packet for owner sign-off. DONE:
  wireframes approved 24/11/2026 (`docs/ux/`), parity matrix `docs/research/parity-matrix.md`.
- R4 New pinned handoff with phases, acceptance tests and release holds. Only then implementation.
  DONE: `docs/HANDOFF.md`, issued 24/11/2026.

## Phase 0 — Falsification spike (authority boundary, containment and one fix loop)

Goal: measure what the authority boundary actually holds, before building. Not "prove the fixer
cannot merge" -- M0 already showed a Contents-write token can merge an unprotected PR. Prove
instead that the broker, the container and the ruleset together stop Code-Shipper's own paths,
and record exactly what they do not stop.

Work:
1. Owner creates (or approves creating) one disposable private test repo and, for `separated`
   mode, one or two bot accounts or fine-grained tokens. The builder never creates GitHub
   accounts or repos itself.
2. Run candidate setups C1–C4 (tech spec §6) with a throwaway script under `spikes/m0/`. C4 runs
   both uncontained and inside the rootless container from `reports/M0-ROOTLESS.md`. Record the
   "approval present + checks green, then fixer merges" case explicitly, whichever way it lands.
3. Build and measure the host inference transport (tech spec §2.4): contained worker calls out
   through a per-run capability; prove it cannot reach GitHub, host files or other host services,
   and that it dies on run end and on cancel. This is a prerequisite, not an assumed feature.
4. Minimal fix loop: a PR with one failing unit test; the contained worker runs one harness
   (Hermes), gets a fix, and the broker pushes with an expected-old-ref lease. CI goes green.

Gate:
- T-SPIKE-01..06 results recorded with the exact token permissions and ruleset settings used.
- Containment acceptance (tech spec §6.1) passes for the chosen backend, with image digest and the
  full mount/env/namespace/network policy in evidence. Contained Hermes + inference broker stays
  UNVERIFIED if it was not run; do not report isolation results as fixing safety.
- One fix loop to green, with the push made by the broker, not by the agent.
- Tech spec §6 updated with the chosen v1 default, or a written "boundary not achievable" with
  the spec change it forces.
Stop for owner review of M0.

## Phase 1 — Core skeleton

Work: package layout (tech spec §1, including `contain/`, `inference/` and `surfaces/` stubs),
config loading/validation, ledger + locks, CodeHost GitHub adapter (reads only), Harness protocol
+ `hermes`, `command` (presets unverified), `openai_compat` adapters, fakes, `code-shipper doctor`
reporting VERIFIED / UNVERIFIED / UNSUPPORTED per backend.

Gate: T-UNIT-LEDGER, T-CONTRACT-02 (recorded), T-CONTRACT-01 for `hermes` and at least one
`command` preset on a real install, T-ADV-07, T-ADV-12; CI matrix (Linux, macOS, Windows) green
on unit tests.

## Phase 2 — Review engine

Work: lenses, verify, rank, posting plan, `code-shipper review <pr> [--level] [--profile]` with
`--dry-run` (prints findings, posts nothing) and a real post via broker `reviewer` role.

### P2-C — honesty pack (owner decisions D10–D15, 24/11/2026)

Work, from `SCOPE-ADDITIONS-2026-11-24.md`:

- C1 path filters with default ignores.
- C2 wrap-up budget reserve: a run forced to exhaust its budget still posts "incomplete" and writes
  the ledger.
- C4 stable fingerprint — path + normalised title + hash of quoted evidence, never the line number.
  **A prerequisite for Phase 3 incremental review (D11).**
- C6 per-finding rationale, collapsed.
- C7 + C8 explicit review state, including the "no findings" case.
- C16 machine-readable verdict block in the review body.
- C20 review cache keyed on (repo, head_sha).

Gate: T-UNIT-* for the fingerprint (shifting code by N lines does not re-post); the wrap-up reserve
test; a review posted with the new body shape; cache hit and miss both exercised.

## Phase 3 — Broker + Steward (on-demand watch)

Work: broker roles/allowlists/exact head/receipts, expected-old-ref lease, uncertain-write
reconciliation; steward classify/contained workspace/fix/scope; comment commands; label; budgets;
`code-shipper watch <pr>` and `stop`. Scope rules come from policy config, not hard-coded paths, and
test-weakening detection is AST-based.

Phase 3 additions from D10–D15: C3 incremental review plus the D11 union rule; C9 coverage summary;
C12 per-PR review cap with a stated pause; C5 profile; C10 CI annotations as untrusted hints.

Gate: T-ADV-02, 03, 04, 06, 08, 09, 10, 11, 14, 15, T-UNIT-BUDGET, T-SCN-01..04 on the test repo,
plus one real contained fix run.

## Phase 4 — Approvals

Work: policy parser + validator (stdlib), facts, evaluator, status + observe comment, agent
approval path under FR-A4 in `separated` mode, re-evaluation on push.

Phase 4 additions from D10–D15: C13 preview/validate config and `approvals.yml` before use; D15d
shared settings plus `code-shipper config effective <repo>` (a repo may tighten, never loosen, a
central security key).

Gate: T-UNIT-POLICY-01..20, T-UNIT-POLICY-PARITY, T-UNIT-EVAL-01..15, T-ADV-01, T-ADV-16, T-ADV-17,
T-SCN-05, plus the D15d loosening refusal with the key named and provenance in `config effective`.

## Phase 4b — Review intelligence (D15a–c)

Work:

- **Merge risk score** (D15a): a 5-level advisory rating with reasons in the review summary.
- **Low-effort / AI-generated PR detection** (D15b): default **off**; when on, one label and one
  explanatory note only.
- **Review memory** (D15c): cue + action + evidence rows; learnt only from write-access feedback
  checked through the host API; off → shadow → on, default shadow; `code-shipper lessons
  list|show|withdraw`; a reversed disposition withdraws the lesson; stored under the state dir,
  harness- and memory-provider neutral.

Gate, one per guardrail: the approvals evaluator **cannot** read the risk score (structural test);
detection defaults off and, when on, makes at most one label and one note and has no close or block
call; feedback from a non-writer creates no lesson; shadow mode changes no output; withdraw removes
the effect on the next review; a reversed disposition withdraws automatically; lessons never touch
the approvals policy or security settings.

## Phase 5 — `/ship` + Hermes plugin + CLI

Work: `code-shipper ship` (plan, explicit publish confirmation, secret scan, branch/commit/push/PR,
review, watch); Hermes plugin: `/ship` (with `watch`, `stop`, `status` sub-args), `hermes
code-shipper` CLI subcommand, CLI and TUI parity; secret scan and redaction. Wireframes must be
owner-approved (R3) before any UI code in this phase.

Gate: T-UNIT-SECRETS-01..10; `/ship` run end to end on the test repo from Hermes CLI and TUI in
an isolated test `HERMES_HOME` (never the live one), transcript in evidence; same flow from the
plain CLI; `hermes plugins validate` clean at the pinned upstream; non-TTY run without a recorded
authorisation fails with a message instead of hanging.

## Phase 5b — Interactive TUI and Desktop surfaces

Work: one shared event and authenticated action service behind all surfaces; keyboard PR inbox and
detail view (diff, findings, checks, policy, activity tabs); Hermes Desktop plugin page and
contextual pane on host routes with a scoped backend, no second Electron process. No merge control
on any surface.

Gate: every real action (review, fix, stop, cancel, retry, inspect) driven from each surface with
screenshots or recordings bound to the candidate build; cancellation, reconnect, stale-head and
denied-permission states demonstrated; views converge after an action or show explicit staleness;
keyboard-only journeys, visible focus and non-colour status labels checked. A screenshot of a page
that calls no backend is NOT integration evidence.

## Phase 6 — Repo watching

Work: `code-shipper repos add/remove/list`, `code-shipper tick`; Hermes script-only cron job
install helper (prints the exact `hermes cron` command for the owner to run; does not install it
into the live Hermes); OS scheduler examples (systemd timer, launchd, Task Scheduler); GitHub
Actions template using `openai_compat` for review + approvals only. Fixing inside Actions stays an
open gap until a contained provider-neutral tool loop exists (tech spec §2.2); the template must
not be presented as parity.

Gate: one hour of watching on the test repo with 3 PRs opened during the window: each reviewed,
one fixed, approvals status on all; no duplicate posts (ledger check); Actions template run once
on the test repo with a test key the owner supplies.

## Phase 7 — Public readiness

Work: README (status per capability, diaktoros credit, provider-terms notes), SECURITY.md,
CONTRIBUTING.md, examples, `T-EVAL-SEEDED` with a held-out half, precision/recall report,
draft catalog YAML (not submitted).

Gate: PRD §5 metrics met or the shortfall stated; secret scan of the whole history clean;
owner decides repo creation, first push and catalog submission (D6). Licence is MIT (D9).

## Report template (`docs/reports/M<n>-REPORT.md`)

```
# M<n> report
Verdict: READY FOR REVIEW | BLOCKED
Commit: <full sha>
Gate items:
- <item>: VERIFIED | PARTIAL | NOT DONE — evidence: <path>
Deviations from spec (with reason):
Open risks:
Owner questions:
Plain-English summary (≤ 8 lines):
```
