# Code-Shipper

Review, fix and get pull requests ready to merge, with any agent harness. Hermes Agent first.

Status: **spec pack and feasibility spikes only — the plugin is not built.**
Build handoff is held: spec revision folded in, pending UX wireframe approval. The canonical
documents below are consistent with `docs/REVISION-2.md`, whose body remains as the audit trail.
Anything described here is a design goal, not a verified guarantee. Code-Shipper will expose no
merge action on any surface.

Code-Shipper is an open-source take on the workflow Anthropic calls Claude Ship, built to work
across agent harnesses (Hermes, Claude Code, Codex, OpenCode, or a direct model API):

- **Review**: several lenses read the change, a verification pass tries to refute each finding,
  and only confirmed findings are posted inline. A review never approves or blocks.
- **Steward**: fixes failing checks, merge conflicts and review comments, posts what it changed
  and how to undo it after every push, and stops on `@code-shipper stop`. It never holds a GitHub
  credential, every write goes through a broker whose allowlist has no merge operation, and no
  surface offers a merge button. A person merges.
- **Approvals**: a policy file in your repo (`.code-shipper/approvals.yml`) says how many people
  must approve which paths. Observe mode first. Letting the agent approve low-risk changes is an
  opt-in flag, off by default, and needs a separate approver identity.
- **Triggers**: `/ship` (or `code-shipper ship`) on your current change, or opt-in watching of
  chosen repositories on your machine or in GitHub Actions.

GitHub only in v1; other code hosts come later through the same interface.

Safety note: the process running the model never gets a GitHub token, and every write goes through
a broker with a per-role allowlist and an exact-head check. Merging is a product constraint: no
surface exposes it. That is not the same as the token being harmless — a Contents-write token can
merge a pull request on an unprotected repository, which is why repos that enable agent approval
need branch rules or a ruleset as the last layer. On your own machine, a fix run that is not
contained can still read your own GitHub login from disk by absolute path; that is why fixing runs
happen in a container with credential files and sockets hidden, and why agent approval is refused unless
the platform doctor reports that container VERIFIED. What is measured is the container basics
(twenty controls, `docs/reports/M0-ROOTLESS.md`); a contained harness run with the host inference
broker is UNVERIFIED, so secure fixing is not yet proven. Details: `docs/tech-spec.md` §2.4, §6.1.

Surfaces: a scriptable CLI, an interactive TUI inbox and a Hermes Desktop page share one state and
action service. Wireframes are in owner review; no UI code is written before they are approved.

Related work: Claude Ship (Anthropic, Claude-only) and
[diaktoros](https://github.com/coe0718/diaktoros) (Hermes-only review loop whose broker design
informs ours; it already has a host-side `human_paths` gate and experimental native macOS, so what
we add is the repository-committed policy with seats and observe/enforce). See
`docs/research/prior-art.md`.

## Documents

| File | What it holds |
|------|---------------|
| `docs/HANDOFF.md` | Start here if you are building it |
| `docs/decisions.md` | Owner decisions (binding), research facts, proposed defaults |
| `docs/prd.md` | Problem, users, requirements, metrics, risks |
| `docs/architecture.md` | C4 diagrams, flows, trust boundaries |
| `docs/tech-spec.md` | Interfaces, numbers, policy grammar, broker, tests |
| `docs/build-plan.md` | Phases, gates, report template |
| `docs/REVISION-2.md` | Revision 2 body, retained as audit trail (folded into the docs above) |
| `docs/reports/` | Spike evidence, including the rootless containment probe |
| `docs/adr/` | Architecture decision records |
| `docs/research/` | Prior art and verified host surfaces |

Licence: MIT (see `LICENSE`).

