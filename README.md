# Code-Shipper

Review, fix and get pull requests ready to merge, with any agent harness. Hermes Agent first.

Status: **Phases 0-7 built against a sandbox repository. Not published.** Readers and writers are
exercised on `Sahil-SS9/code-shipper-sandbox`; the TUI and Desktop surfaces are awaiting a redesign
and visual sign-off and are not part of any distribution yet. The canonical documents are
`docs/HANDOFF.md`, `docs/tech-spec.md`, `docs/decisions.md` and the per-phase reports in
`docs/reports/`. Code-Shipper exposes no merge action on any surface, and that is asserted by tests
rather than promised by documentation.

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
- **Triggers**: `/ship` (or `code-shipper ship`) on your current change, or opt-in polling of
  chosen pull requests while you run it.

**Polling, precisely.** There are no webhooks in v1 and no public endpoint. When you start a
watcher it checks each pull request **once every N seconds** (30 by default) for a new head, and
does nothing between checks. It is not a continuous stream, it does not react instantly, and a push
made one second after a check is seen on the next one. The watcher stops itself at a deadline you
set, on `@code-shipper stop`, or when you remove the stop label — and it checks those signals at the
start of every round, never mid-round.

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

## Exit codes

Every command that reports a review result uses the same four codes, so a wrapper can branch on them:

| Code | Meaning |
|---|---|
| `0` | **clean** — the review completed and found nothing to fix |
| `1` | **findings** — the review completed and found something to fix |
| `2` | **error** — the run failed; nothing was reviewed |
| `3` | **incomplete** — the review did not finish; this is not a clean result |

`3` is deliberately distinct from `2`. An incomplete review is not a tool failure and it is not a clean
result: it can be retried, and treating it as either success or error would be wrong in opposite
directions. `code-shipper --help` prints the same list.

## Protect your default branch

**Do this before you let Code-Shipper fix anything on a repository you care about.**

A token that can write contents can reach the merge endpoint. GitHub's branch protection gates the
*PR* — its approvals and its required checks — not the identity of whoever presses merge. So if the
PR is eligible, a write-capable token can merge it, and Code-Shipper assumes exactly that rather than
hoping otherwise.

Two rules:

1. **Protect your default branch.** Require a pull request, require at least one approval from
   someone other than the author, require the checks you rely on, and do not leave an admin bypass in
   place for the account whose token Code-Shipper uses.
2. **Never give the agent a merge-capable token.** Code-Shipper is built so it cannot leak one — the
   broker holds the token outside the container, the agent never sees it, and there is no merge
   operation anywhere in the product. Do not undo that by exporting a token into the environment the
   agent runs in, or by mounting one into its container.

The defence does not rely on a token being unable to merge. It relies on the token never reaching the
agent, and on your branch rules deciding what happens if one ever did.

Surfaces: a scriptable CLI, a terminal board and a Hermes Desktop page share one state and action
service. All three read the **stored review state** for a pull request — the same state `review`, `poll`
and `watch` write. Nothing draws sample data unless you ask for it with `--demo`, and then every surface
prints "demo data" on the output.

```
code-shipper status                          # everything reviewed, from stored state
code-shipper status --repo owner/name        # one repository
code-shipper ui --repo owner/name --pr 25    # the terminal board for a real review
code-shipper ship-page --repo owner/name --pr 25 --out board.html
code-shipper status --demo                   # the sample board, labelled as such
```

Inside Hermes the plugin provides `/ship <owner/repo> <pr> <subcommand>`, a thin wrapper over the CLI:
`review`, `board`, `fix`, `watch`, `stop`, `status`, `publish`. `/ship ... board` draws the stored review;
there is no separate Hermes Desktop panel — the desktop surface is the HTML page `ship-page` writes, or
the terminal board. That is a deliberate scope line, not an omission: a second UI inside the Hermes
desktop would be a second place holding presentation logic.

`code-shipper review` reviews and **stores** the result without commenting on GitHub; `watch` and `poll`
are the posting path and post the same review. `code-shipper fix` without `--dry-run` prints the plan and
directs you to `watch`, which owns the round, the budget and the stop signal rather than duplicating them.

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

## Tests

```
./scripts/run_tests.sh          # unit tests: fast, no Docker, no network
./scripts/run_tests.sh --full   # plus the contained-run battery (rootless Docker, cached image)
```

Standard library `unittest`; the runtime has no third-party dependencies.

Licence: MIT (see `LICENSE`).

