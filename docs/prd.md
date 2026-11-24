# Code-Shipper — Product Requirements

Status: draft for build. Binding decisions: `docs/decisions.md` (D1–D8 win every conflict).

## 1. Problem

Agents now write much of the code in a pull request, but everything after the PR is opened is
still manual: read the review, switch back to old code, push a fix, wait for CI, chase an
approver. Anthropic's Claude Ship automates this (review, fix, policy-based approval), but only
for Claude, only on GitHub, and only on Team/Enterprise plans.

Evidence and its limits:
- Claude Ship's existence and design are verified from Anthropic's docs (decisions F1–F4).
  Anthropic reports its own team's merged PRs more than doubled with the share reverted down by
  over 40%. That is a vendor claim, unverified by us, about a team with Ship as one layer of many.
- The closest open-source equivalent (diaktoros, F5) proves demand among Hermes users (328 merged
  PRs on its own repo), but is Hermes-only and centred on a repo-wide unattended loop.
- We have no user research beyond the owner. Demand from non-Hermes harness users is assumed.

## 2. Users

| User | Situation | What they need |
|------|-----------|----------------|
| Solo agent-heavy developer (primary; the owner) | Opens many agent-written PRs a day across repos | `/ship` from the session they built the change in; fixes without babysitting; never an unwanted merge |
| Small team maintainer | Wants consistent review and a written rule for who approves what | Repo-wide watching, an approval policy in the repo, observe mode first |
| Users of other harnesses (Codex, Claude Code, OpenCode) | Want the same flow without switching harness | A CLI that works from any harness, using the account they already pay for |

Out of scope for v1: GitLab/Bitbucket (D4), hosted SaaS, merge automation of any kind (D1).

## 3. Functional requirements

IDs are referenced by the tech spec and tests.

Review
- FR-R1. Review a PR diff with the surrounding code through several lenses (correctness,
  security, edge cases, tests/regressions, API/contract), at Quick, Standard or Deep level (K7).
- FR-R2. Every candidate finding goes through a verification pass that tries to refute it from
  the code before it is posted. Unverified findings are dropped, not posted.
- FR-R3. Deduplicate and rank findings: Important, Nit, Pre-existing.
- FR-R4. Post findings as inline comments on the lines concerned plus one summary comment.
  A review never approves or blocks.
- FR-R5. Read review instructions (`REVIEW.md`, `.code-shipper/review.md`) only from the base
  branch, never from the PR's own changes.

Steward (fix loop)
- FR-S1. Work on: failing checks whose cause is in code the PR touches; merge conflicts (merge
  base in, resolve); human review comments (small, specific changes, or a reply when the request
  is large/design-level); Important findings from Code-Shipper or other review bots.
- FR-S2. After every push, comment what changed and how to undo it (`git revert <sha>`).
- FR-S3. Steering: `@code-shipper <instruction>` from a user with write access; `@code-shipper
  stop` or removing the `code-shipper-watching` label stops it.
- FR-S4. Budgets fail closed (K4): rounds, pushes, wall clock, spend. When a budget is spent the
  PR is left labelled and a comment says why.
- FR-S5. Never approve, merge, enable auto-merge, force-push, delete branches, push tags or change
  branch protection. Enforced outside the model (see NFR-1).
- FR-S6. Skip drafts, fork PRs, bot-authored PRs and authors without write access unless the
  repo config opts in (drafts only).

Approvals (policy)
- FR-A1. Read `.code-shipper/approvals.yml` (K2) from the default branch only. Rules name
  paths, an optional condition, and a verdict: how many people from which list, and whether the
  agent approves, comments or blocks. The strictest applicable verdict wins.
- FR-A2. Protected files (the policy itself, CODEOWNERS, CI workflow files, `.gitattributes`,
  `.gitmodules`, symlinks, submodule pointers, review instructions, agent instruction files)
  always need a person.
- FR-A3. Modes: `observe` (report only) and `enforce`. Report a commit status
  `code-shipper/approvals` (K5) whose description says who still has to approve.
- FR-A4. Agent approval happens only when ALL hold: the local config flag
  `approvals.allow_agent_approval` is true (off by default, D1); the policy is in `enforce`; every
  applicable rule says `approves`; the latest Code-Shipper review at the current head has no open
  Important finding; the approver identity differs from the PR author and from the fixer.
- FR-A5. Re-evaluate on every push. A new head invalidates the agent's earlier approval (dismiss
  it where the identity can).

Triggers
- FR-T1. `/ship` (Hermes slash command) and `code-shipper ship` (CLI, any harness): if needed
  create a branch, commit, secret-scan, push and open the PR as the user; then review; then
  watch. A plan is printed first with a cancel window.
- FR-T2. `code-shipper watch <pr>` (and `/ship watch <pr>`): start the fix loop on an open PR
  with no review first (equivalent of Claude's `/steward`).
- FR-T3. Opt-in repo watching (D2): `code-shipper repos add <owner/repo>`; a scheduled tick
  starts review/steward/approvals on new and updated PRs. Runtime: local scheduler by default,
  GitHub Actions optional (D5). In v1 the Actions runtime does review and approvals only; fixes
  need a harness with a tool loop and run locally (tech spec §2.2, `openai_compat`).

Harness and host
- FR-H1. Harness adapters behind one interface (D3, K8). Default is the user's existing harness
  login; a direct-API adapter is optional.
- FR-H2. Code host behind one interface; GitHub adapter only (D4).

Hermes integration
- FR-X1. Hermes plugin: `/ship` slash command, `hermes code-shipper ...` CLI subcommand, a
  script-only cron job for repo watching. Works in the classic CLI and the TUI (parity).

## 4. Non-functional requirements

- NFR-1 Authority boundary. The process that runs the model never holds a credential that can
  approve or merge. GitHub writes go through a broker that allows only an explicit list of
  operations per role. The strong mode uses a separate identity for the approver. Claims about
  what is "enforced" must say by what: GitHub permissions, the broker, or only instructions.
- NFR-2 Untrusted input. PR text, diffs, comments, CI logs and repo files are untrusted. They
  never change policy, budgets, identity or the allowed-operations list.
- NFR-3 Exact head. Every write is checked against the PR head it was computed for; a moved head
  aborts the write.
- NFR-4 Idempotent and resumable. State is kept per PR in a local ledger; a crash or restart does
  not double-post or double-push.
- NFR-5 Portability. Core runs on Linux, macOS and Windows with Python 3.11+ and `git`; the
  Hermes plugin wherever Hermes runs.
- NFR-6 No telemetry. No data leaves the machine except to the code host and the chosen model
  provider.
- NFR-7 Secrets. Nothing secret is logged, committed, or passed into model prompts (redact
  tokens in CI logs before prompting).

## 5. Success metrics (proposed before build, measured in Phase 7)

| Metric | Target |
|--------|--------|
| Seeded-bug recall at Standard (held-out set) | ≥ 60% of seeded Important bugs found |
| Precision at Standard (posted Important findings that are real) | ≥ 80% |
| False findings on clean PRs (benign set) | ≤ 1 Important per 10 clean PRs |
| Fix loop success on the scripted scenario set (failing test, conflict, review comment) | ≥ 70% reach green within budget |
| Authority-boundary attack set (approve/merge/force-push attempts through any path) | 0 successes |
| Unwanted merges in any test | 0 |

## 6. Risks

| Risk | Mitigation |
|------|-----------|
| Agent approves or merges via a path a hook doesn't see (shell, `gh api`, GraphQL), or by reading the developer's own token from disk | NFR-1: credential separation, not command matching; OS sandbox hiding credential files, required for agent approval; GitHub rulesets as the last layer. Phase 0 spike must prove it. Residual risk for unsandboxed runs stated in the README. |
| Prompt injection in PR content steers the fixer | NFR-2; broker allowlist; budgets; fixer scope limited to PR files plus tests. |
| Provider terms forbid using subscription logins for automation | Each adapter documents its provider's terms; direct-API adapter as the fallback (D3). |
| Noise: reviews full of false positives | FR-R2 verification; precision metric gate before public release. |
| Public from day one (D6) exposes an unfinished tool | README states status per phase; agent approval stays off by default; no catalog entry before Phase 7. |
| Overlap with diaktoros | Positioning in README: harness-neutral, `/ship`-first, repo policy file. Credit it. |

## 7. Open questions

See `docs/decisions.md` → Open questions.
