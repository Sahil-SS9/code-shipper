# Code-Shipper

Review, fix and get pull requests ready to merge, with any agent harness. Hermes Agent first.

Status: **spec pack only — nothing is built yet.** Humans always merge.

Code-Shipper is an open-source take on the workflow Anthropic calls Claude Ship, built to work
across agent harnesses (Hermes, Claude Code, Codex, OpenCode, or a direct model API):

- **Review**: several lenses read the change, a verification pass tries to refute each finding,
  and only confirmed findings are posted inline. A review never approves or blocks.
- **Steward**: fixes failing checks, merge conflicts and review comments, posts what it changed
  and how to undo it after every push, and stops on `@code-shipper stop`. It cannot approve or
  merge: it never holds a credential that can.
- **Approvals**: a policy file in your repo (`.code-shipper/approvals.yml`) says how many people
  must approve which paths. Observe mode first. Letting the agent approve low-risk changes is an
  opt-in flag, off by default, and needs a separate approver identity.
- **Triggers**: `/ship` (or `code-shipper ship`) on your current change, or opt-in watching of
  chosen repositories on your machine or in GitHub Actions.

GitHub only in v1; other code hosts come later through the same interface.

Safety note: the agent process never gets a GitHub token, and in the separated-identity mode
the fixer's own token cannot approve or merge. But on your own machine, an unsandboxed agent running as your user could still read
your own GitHub login from disk. That is why agent approval needs a sandboxed fix run (Linux
bubblewrap, macOS Seatbelt, or a container) and why GitHub branch rules stay the final guard.
Details: `docs/tech-spec.md` §6.1.

Related work: Claude Ship (Anthropic, Claude-only) and
[diaktoros](https://github.com/coe0718/diaktoros) (Hermes-only review loop whose broker design
informs ours). See `docs/research/prior-art.md`.

## Documents

| File | What it holds |
|------|---------------|
| `docs/HANDOFF.md` | Start here if you are building it |
| `docs/decisions.md` | Owner decisions (binding), research facts, proposed defaults |
| `docs/prd.md` | Problem, users, requirements, metrics, risks |
| `docs/architecture.md` | C4 diagrams, flows, trust boundaries |
| `docs/tech-spec.md` | Interfaces, numbers, policy grammar, broker, tests |
| `docs/build-plan.md` | Phases, gates, report template |
| `docs/adr/` | Architecture decision records |
| `docs/research/` | Prior art and verified host surfaces |

Licence: to be decided before the first public release.
