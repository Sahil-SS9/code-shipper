# Decisions

Status: current. REVISION-2 is folded into this pack as of 24/11/2026; its body remains in
`REVISION-2.md` as an audit trail. Owner requires first-class CLI, interactive TUI and Desktop.
MIT is accepted (D9). The original build handoff is held: spec revision is folded in, UX
wireframes are pending owner approval.

Owner decisions are binding and win every conflict with any other document in this repo.
KENSEI defaults are proposals: a builder may proceed on them, but nothing that depends on a
default may be published until the owner accepts it.

Source: owner grill, 24/11/2026 (kanban core board, card t_135247fa).

## Owner decisions (binding)

| ID | Decision | Owner's words |
|----|----------|---------------|
| D1 | Two authority modes. Default: Code-Shipper reviews and fixes; humans approve and merge. A config flag, off by default, lets it approve low-risk changes when the repository's approval policy allows. Merging is always done by a human, in both modes. | "have both with one and default the other behind a config flag" |
| D2 | Two triggers: on-demand `/ship` for one PR, and opt-in automatic watching of selected repositories. | "option 3" |
| D3 | Reviews and fixes run on the user's existing harness accounts by default (Hermes, Codex, Claude Code, OpenCode...). Direct model APIs with their own keys are an optional alternative. Every adapter respects its provider's terms. | "opton 3" |
| D4 | Code hosts sit behind one interface. Only the GitHub adapter is built in v1. GitLab and others come later without a rewrite. | "okay option 3" |
| D5 | Automatic watching runs on the user's machine by default (Hermes scheduler for Hermes users). GitHub Actions is an optional runtime, which needs direct API keys. | "option 3" |
| D6 | Public open source from day one. Creating the public repository and the first push still wait for the owner's explicit go. | "option 2" |
| D7 | Code-Shipper is built standalone, focused on what the closest prior art (diaktoros) lacks: a harness-neutral core, a repository-committed approval policy file, on-demand `/ship` from the user's own session, and supported macOS/Windows use (see F5 for the corrected comparison). Its MIT-licensed design may be studied; no code is copied without licence attribution. | "option 1" |
| D8 | Name: Code-Shipper. | "name = Code-Shipper" |
| D9 | Licence: MIT, "Copyright (c) 2026 Sahil Saghir". Supersedes K6. | "GO WITH mit" |
| D10 | Review modes are three **orthogonal** settings: depth `review.level` (quick/standard/deep, existing), volume `review.profile` (quiet/standard/assertive), and trigger `review.trigger` (manual/open/on-push/off). `profile` defaults to **standard** everywhere. `--level` and `--profile` are CLI flags; trigger is config only. | Owner approved 24/11/2026 |
| D11 | Incremental review ships in v1. An on-push review covers only commits since `last_reviewed_head`, which is set **only** by a complete review — never by ledger activity. Findings are deduplicated across pushes by a stable fingerprint, and **not** the line number. For approvals, `review.important_open` is the **union** of open important findings over the whole PR, never just the last pass. **Amendment (25/11/2026):** the fingerprint is **path + hash of the quoted evidence**, without the title. Measured: two real reviews over identical code produced materially different titles for the same finding, so including the title split one finding into two — the exact duplicate this entry exists to prevent. Evidence carries identity; the title is used only when a finding quotes nothing. **Because two different problems quoting the same line now share a fingerprint and merge, a merge must not lose information: the surviving finding takes the highest severity, names the merged-away titles in its body ("Also noted on this line: …"), and the verdict block records the merged count.** | Owner approved 24/11/2026; amendment approved 25/11/2026 |
| D12 | `pre_existing` findings are **not posted**. The review summary states how many were omitted. | Owner approved 24/11/2026 |
| D13 | Review state is stated in the review body now. A GitHub check run or status arrives in Phase 5 and needs a new, separately tested broker role. | Owner approved 24/11/2026 |
| D14 | No pre-merge custom checks in v1. Recorded as an ADR: they are model judgement on author-controlled text and they overlap the approval policy. | Owner approved 24/11/2026 |
| D15a | **Merge risk score** (advisory): a 5-level rating with reasons in the review summary. **GUARDRAIL: advisory only.** The approvals evaluator must not read it, enforced by a structural test that the policy evaluator has no access to the field. Never named or styled as a gate. | Owner approved 24/11/2026 |
| D15b | **Low-effort / AI-generated PR detection.** **GUARDRAIL: default OFF.** When on it adds one label and one explanatory note only. It never closes, blocks, or lowers review effort, and never states as fact that something is AI-written — "signals of", with the signals listed. | Owner approved 24/11/2026 |
| D15c | **Review memory** (evidence-gated shape): each lesson is a cue + action + at least one evidence row (PR, comment, who, when). **GUARDRAILS:** lessons are learnt only from feedback by accounts with write access, checked through the host API rather than claimed in text; modes off → shadow (report what would change, apply nothing) → on, default **shadow**; every lesson is listable and withdrawable (`code-shipper lessons list|show|withdraw`); a reversed disposition withdraws the lesson; lessons never touch the approvals policy or security settings; stored locally under the state dir, harness- and memory-provider-neutral. | Owner approved 24/11/2026 |
| D15d | **Shared settings across repos**: a central config (local path or a designated repo) that repos inherit. **GUARDRAIL: a repo may TIGHTEN but never LOOSEN** a central security key — `allow_agent_approval`, protected paths, scope rules, budget ceilings, the trigger off-switch. A loosening attempt is refused with the key named. `code-shipper config effective <repo>` prints the merged result and where each value came from. | Owner approved 24/11/2026 |

Source for D10–D15: owner decisions 24/11/2026, folded in from
`SCOPE-ADDITIONS-2026-11-24.md`; underlying research in `docs/research/review-modes-scope.md`
(recommendation IDs C1–C20). ARTEX is AGPL-3.0: ideas only, no code adopted.

## Not adopted (binding)

- **Any switch in PR-authored text** that disables, pauses or reconfigures its own review — a
  description, a commit message, or a comment by a non-writer. This is the same principle as ADR 0002
  (authority comes from credentials, not from text) and as the framing note in tech spec §7.
- **Any approve or merge path.** Humans always merge.

## Research facts this pack relies on

All verified 24/11/2026. Sources are in `docs/research/`.

- F1. Claude Ship is three agents: Code Review (finds and verifies, never approves or blocks),
  PR Steward (pushes fixes for failing checks, conflicts and review comments, designed not to
  approve or merge) and Claude Approvals (a check driven by `.claude/approvals.yml` on the
  default branch). GitHub only. Team/Enterprise only.
- F2. Claude's local `/ship` commits, pushes and opens the PR as the user, runs a local review,
  then "watches" the PR from the user's session. During a watch Claude Code blocks merge,
  approve, auto-merge and force-push commands by matching command text. Anthropic's own docs say
  a command written another way can pass those rules, and tell users to rely on GitHub branch
  rules for real enforcement.
- F3. In the hosted PR Steward, approving, changing branch protection, writing check runs,
  deleting branches and pushing tags are refused by Anthropic's service, not by the model.
  Merging and force-pushing are left to GitHub's branch rules.
- F4. Approvals takes the strictest verdict of all rules that apply, reads policy only from the
  default branch, and always asks for a person on "protected files" (the policy itself,
  CODEOWNERS, git attributes, submodules, symlinks, review instructions).
- F5. diaktoros (Hermes catalog, MIT) already runs a fixer and a reviewer as separate GitHub
  identities, keeps agents credential-free in bubblewrap and sends every GitHub write through a
  host broker. It is Hermes-only; Linux is its supported platform and native macOS is an
  experimental opt-in. It has a host-side `human_paths` glob list that withholds the reviewer's
  approval on matching paths, but no repository-committed policy that sets how many people must
  approve each path, with conditions and observe/enforce modes.
  Correction (24/11/2026): when D7 was asked, the owner was told diaktoros had "no per-path
  approval policy" and was "Linux-only". Both were overstated; the gap D7 targets still exists in
  the narrower form above. The owner is told in the pack hand-over message.
- F6. Hermes exposes the plugin seams this pack needs (`register_command`,
  `register_cli_command`, `register_hook`, `ctx.llm`, script-only cron jobs). Verified at
  NousResearch/hermes-agent upstream/main `4f676af2cfee3fb41b2047bccd2dfa39dbd73bd7`.

## Corrections folded in (24/11/2026)

These override the original pack wherever it disagreed. The originals stay in `REVISION-2.md` as
the audit trail.

- Rootless Docker is available and functional on this host; an earlier "not in the docker group,
  so Docker is unavailable" diagnosis was wrong. An owner-authorised probe passed twenty controls
  inside a rootless container (`reports/M0-ROOTLESS.md`). Basic filesystem, process and network
  separation is MEASURED. A contained Hermes run plus the host inference broker is UNVERIFIED, so
  secure fixing is not proven.
- The fixer token CAN merge on an unprotected repository; it merged sandbox PR 2. Human-only
  merging is a product and broker constraint, never a claim that the token is incapable. The 422
  self-approval refusal and the untested "merge once an approval exists" case stay as recorded.
- diaktoros does have a host-side `human_paths` per-path gate and experimental native macOS. The
  D7 gap is the repository-committed policy with seats, conditions and observe/enforce (F5).
- Authority is enforced by credentials and the broker allowlist, never by matching command text
  (ADR 0002).
- Fixing inside GitHub Actions is an open gap: our direct-API adapter is review-only, and a
  workflow file is not parity.
- CLI, interactive TUI and Desktop are all first-class over one shared state, event schema and
  authenticated action service, with no merge control on any surface.
- No compliance claim (HIPAA, FERPA, ZDR, CMEK) follows from Claude Ship's plan exclusions.

## KENSEI defaults (proposed, owner may override)

| ID | Default | Why |
|----|---------|-----|
| K1 | Core written in Python 3.11+, standard library only, so one repo is both the Hermes plugin and a pip/pipx-installable CLI. | Hermes plugins are Python; no dependency install step inside Hermes. |
| K2 | Policy file at `.code-shipper/approvals.yml`. Own format, inspired by Claude Approvals, not compatible with it. | Avoids implying Anthropic compatibility. An importer can come later. |
| K3 | PR comment command prefix `@code-shipper`; watch label `code-shipper-watching`. | Mirrors the Ship UX. |
| K4 | Budgets: 3 fix rounds per trigger, 10 pushes per PR per day, 45 minutes wall clock per run, per-day model spend cap set by the user (no default spend). An unknown spend is reported as unknown, never zero, and a run whose cap the adapter cannot enforce or reserve is refused. | Fail closed; numbers are tunable. Subscription wall/turn/token bounds are not a provider billing cap. |
| K5 | v1 reports the policy verdict as a commit status (`code-shipper/approvals`), not a check run. | Commit statuses work with a personal token; check runs need a GitHub App. |
| K6 | Superseded by D9 (MIT accepted). | — |
| K7 | Review levels: Quick = 1 combined lens, Standard = 3 lenses, Deep = 5 lenses. Every level runs the verification pass (FR-R2): Quick reduces lenses, never verification. Deep adds a test-run where a test command is configured. | Mirrors Ship's three levels with a concrete meaning, without letting a cheap run post unverified findings. |
| K8 | Harness adapters in v1: Hermes (native), a generic command adapter with presets for Claude Code, Codex and OpenCode, and an OpenAI-compatible direct-API adapter. Presets are "best effort" until each passes the adapter contract test on a real install. | D3 without promising untested harnesses. |

## Open questions (with recommendations)

1. (Closed: licence is MIT, D9.)
2. GitHub identity setup for the strong mode: second bot account vs GitHub App. Recommend a
   second account with a fine-grained token for v1, a GitHub App later. Decided by the Phase 0
   spike result.
3. Catalog submission timing. Recommend after Phase 7 and the owner's release go.

## D16 — release-blocking decisions (owner, 26-27/11)

Recorded with their state at the time of the release-prep round.

| ID | Decision | State after this round |
|----|----------|------------------------|
| D16a | Code-Shipper never merges. Every surface says so in one sentence: "Code-Shipper never merges. Merge on GitHub." | **Done.** One constant in `surfaces/mantra.py`, rendered by the Desktop page and footer, the TUI footer at both widths, and the CLI review summary. It previously existed in five different phrasings, and the CLI said nothing at all. |
| D16b | Enabling agent approval is a second step: type the repository name back, bound to the `approvals.yml` hash, with no skip flag. | **Done.** `config confirm-agent-approval`. The record is bound to repo and rules hash; editing a rule turns approval off again. No `--yes`/`--force`/`--skip` exists, and with no terminal it refuses. |
| D16c | `ui` accepts the PR number positionally, as `review` does; no two commands read as the same tool. | **Done.** A number is a PR, a known word is a demo state, an unknown word is refused with the valid list. `ship-ui` now says it is an alias of `ui` with narrower defaults. |
| D16d | The README carries a first-review walkthrough that a stranger can follow: which AI, what it costs, dry run first, real exit codes. | **Done.** Every command in it was run as written, with the output quoted. The stale "awaiting sign-off" status line and the 1,236 test count are fixed. |
| D16e | Receipts are kept 30 days per repository. Never prune a receipt for an open PR or an unreconciled/uncertain write. Prune at command start, not as a daemon, and log it. | **Done.** `code_shipper/receipts.py`, wired into command start. Both exceptions beat age, an unreadable receipt is kept because it cannot be shown safe to delete, and pruning one repo never enters another. |
| D16f | The inbox label must not read as "ready to merge" without saying who merges. | **Already met**, unchanged: the group says "Ready for you to merge" and appears only when the approval rules are met. The owner confirmed keeping it over "Ready for human review", which is less accurate at that state. |

Memory stays **off by default in v1** (`shadow`); users opt in with `--memory-mode apply`. That is
unchanged by this round, and the lesson read-path fix it depends on is Fix 6 above.

```text
Addendum on D16b and D16e: both are second gates on top of existing checks, not replacements. Agent
approval still requires separated identities and a verified container; a receipt being within retention
says nothing about whether its write succeeded.
```
