# Code-Shipper — Build Plan

Rules for every phase:
- Do ONE phase, then stop and write `docs/reports/M<n>-REPORT.md` (template at the end).
  The next phase waits for the owner's or KENSEI's GO.
- Each gate item is labelled VERIFIED / PARTIAL / NOT DONE with an evidence path.
- Evidence (logs, command output) goes under `docs/reports/evidence/M<n>/`, redacted. No tokens,
  no real user history.
- Long-run acceptance is capped at one hour per phase.
- No remote push, repo creation, catalog submission or live Hermes install/enable
  without the owner's explicit go (see `docs/HANDOFF.md`).

## Phase 0 — Falsification spike (authority boundary + one fix loop)

Goal: prove or disprove that the fixer cannot approve or merge through any path, before building.

Work:
1. Owner creates (or approves creating) one disposable private test repo and, for `separated`
   mode, one or two bot accounts or fine-grained tokens. The builder never creates GitHub
   accounts or repos itself.
2. Run candidate setups C1–C4 (tech spec §6) with a throwaway script under `spikes/m0/`, C4
   both unsandboxed and inside bubblewrap with credential paths hidden (§6.1).
3. Minimal fix loop: a PR with one failing unit test; a script runs one harness (Hermes) in a
   scrubbed env in a worktree, gets a fix, and pushes via a minimal broker. CI goes green.

Gate:
- T-SPIKE-01..05 results recorded with the exact token permissions and ruleset settings used.
- One fix loop to green, with the push made by the broker, not by the agent.
- Tech spec §6 updated with the chosen v1 default, or a written "boundary not achievable" with
  the spec change it forces.
Stop for owner review of M0.

## Phase 1 — Core skeleton

Work: package layout (tech spec §1), config loading/validation, ledger + locks, CodeHost GitHub
adapter (reads only), Harness protocol + `hermes`, `command` (presets unverified), `openai_compat`
adapters, fakes, `code-shipper doctor`.

Gate: T-UNIT-LEDGER, T-CONTRACT-02 (recorded), T-CONTRACT-01 for `hermes` and at least one
`command` preset on a real install, T-ADV-07, T-ADV-12; CI matrix (Linux, macOS, Windows) green
on unit tests.

## Phase 2 — Review engine

Work: lenses, verify, rank, posting plan, `code-shipper review <pr> [--level]` with
`--dry-run` (prints findings, posts nothing) and a real post via broker `reviewer` role.

Gate: T-ADV-05, T-ADV-13 (reviewer approve refused), a dry-run on 5 seeded PRs with findings
listed in the report, one real posted review on the test repo.

## Phase 3 — Broker + Steward (on-demand watch)

Work: broker roles/allowlists/exact head/receipts; steward classify/worktree/fix/scope;
comment commands; label; budgets; `code-shipper watch <pr>` and `stop`.

Gate: T-ADV-02, 03, 04, 06, 08, 09, 10, 11, 14, 15, T-UNIT-BUDGET, T-SCN-01..04 on the test repo.

## Phase 4 — Approvals

Work: policy parser + validator (stdlib), facts, evaluator, status + observe comment, agent
approval path under FR-A4 in `separated` mode, re-evaluation on push.

Gate: T-UNIT-POLICY-01..20, T-UNIT-POLICY-PARITY, T-UNIT-EVAL-01..15, T-ADV-01, T-ADV-16, T-ADV-17,
T-SCN-05.

## Phase 5 — `/ship` + Hermes plugin + CLI

Work: `code-shipper ship` (plan, cancel window, secret scan, branch/commit/push/PR, review,
watch); Hermes plugin: `/ship` (with `watch`, `stop`, `status` sub-args), `hermes code-shipper`
CLI subcommand, CLI and TUI parity; secret scan and redaction.

Gate: T-UNIT-SECRETS-01..10; `/ship` run end to end on the test repo from Hermes CLI and TUI in
an isolated test `HERMES_HOME` (never the live one), transcript in evidence; same flow from the
plain CLI; `hermes plugins validate` clean at the pinned upstream.

## Phase 6 — Repo watching

Work: `code-shipper repos add/remove/list`, `code-shipper tick`; Hermes script-only cron job
install helper (prints the exact `hermes cron` command for the owner to run; does not install it
into the live Hermes); OS scheduler examples (systemd timer, launchd, Task Scheduler); GitHub
Actions template using `openai_compat` for review + approvals.

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
