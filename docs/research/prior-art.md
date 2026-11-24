# Prior art (checked 24/11/2026)

Verdict: crowded on "an agent reviews or fixes PRs"; thin on "harness-neutral review → fix →
policy approval with a repo-committed approval policy".

| Name | What it does | Where | Overlap | Gap vs Code-Shipper |
|------|--------------|-------|---------|---------------------|
| Claude Ship (Anthropic) | Code Review + PR Steward + Claude Approvals; `/ship` and `/steward` in Claude Code | https://code.claude.com/docs/en/claude-ship | The reference design | Claude-only, GitHub-only, Team/Enterprise, hosted |
| diaktoros (coe0718, MIT, 16★, active) | Unattended Hermes review loop: distinct fixer/reviewer GitHub identities, credential-free bubblewrap agents, host broker, capped rounds, adjudicator, `diaktoros/review` check, never merges; host-side `human_paths`; experimental native macOS | https://github.com/coe0718/diaktoros, Hermes catalog v0.6.1 | Steward + review loop, broker design | Hermes-only, Linux supported; no repo-committed seat policy; repo-wide loop rather than `/ship`-first |
| ci-red-to-green (kinnuworks) | Watches GitHub Actions; agent fixes a clone; PR offered after proof of fail→pass | Hermes catalog | CI fixing | Hermes-only; CI only; no review/approvals |
| foreman | Deterministic gate blocking commit/push/PR when tests are weakened | Hermes catalog | Push gating idea | Not a PR lifecycle tool |
| ship-it (GregoireF) | `ship` CLI: branch/commit/PR per repo conventions, secret scan, drafts-only via MCP for agents | https://github.com/GregoireF/ship-it | `/ship` front half | No review, fix loop or approvals |
| navox-labs/agents | Claude Code agent set incl. a "Shipper" (tests, changelog, version bump, PR) | https://github.com/navox-labs/agents | Naming / PR creation | Claude Code only; no steward/approvals |
| Shippable (Automattic, prototype) | Local-first review desk for agent diffs; MCP | https://github.com/Automattic/shippable-code-review | Review | Read-only on GitHub; no fixes/approvals |
| REA (morluto) | Reverse-engineering CLI + MCP for 14 harnesses via `setup --client` | https://github.com/morluto/rea | Architecture precedent for harness neutrality | Different domain |

Name check: no repo or catalog entry named `code-shipper` found (GitHub web search and
`plugin-catalog/` grep at Hermes upstream `4f676af`).

Do-it-yourself baseline: a user can already run `claude /ship` (Claude-only) or wire diaktoros
(Hermes-only, Linux). What Code-Shipper adds: one tool across harnesses, a policy file in the repo
that states who must approve what, and `/ship` from whichever harness the user is in.
