# Decisions

Current correction/addendum: `REVISION-2.md`. Owner now requires first-class CLI, interactive
TUI and Desktop. MIT is accepted (D9). Proposed defaults K4/K7 and authority/policy descriptions
that conflict with the addendum are superseded; the original build handoff is held.

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

## KENSEI defaults (proposed, owner may override)

| ID | Default | Why |
|----|---------|-----|
| K1 | Core written in Python 3.11+, standard library only, so one repo is both the Hermes plugin and a pip/pipx-installable CLI. | Hermes plugins are Python; no dependency install step inside Hermes. |
| K2 | Policy file at `.code-shipper/approvals.yml`. Own format, inspired by Claude Approvals, not compatible with it. | Avoids implying Anthropic compatibility. An importer can come later. |
| K3 | PR comment command prefix `@code-shipper`; watch label `code-shipper-watching`. | Mirrors the Ship UX. |
| K4 | Budgets: 3 fix rounds per trigger, 10 pushes per PR per day, 45 minutes wall clock per run, per-day model spend cap set by the user (no default spend). | Fail closed; numbers are tunable. |
| K5 | v1 reports the policy verdict as a commit status (`code-shipper/approvals`), not a check run. | Commit statuses work with a personal token; check runs need a GitHub App. |
| K6 | Superseded by D9 (MIT accepted). | — |
| K7 | Review levels: Quick = 1 combined lens, no separate verify; Standard = 3 lenses + verify; Deep = 5 lenses + verify + test-run where a test command is configured. | Mirrors Ship's three levels with a concrete meaning. |
| K8 | Harness adapters in v1: Hermes (native), a generic command adapter with presets for Claude Code, Codex and OpenCode, and an OpenAI-compatible direct-API adapter. Presets are "best effort" until each passes the adapter contract test on a real install. | D3 without promising untested harnesses. |

## Open questions (with recommendations)

1. (Closed: licence is MIT, D9.)
2. GitHub identity setup for the strong mode: second bot account vs GitHub App. Recommend a
   second account with a fine-grained token for v1, a GitHub App later. Decided by the Phase 0
   spike result.
3. Catalog submission timing. Recommend after Phase 7 and the owner's release go.
