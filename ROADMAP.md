# Roadmap

What is built, what is deliberately not, and what is planned. Written so a reader can tell the difference
between "not yet" and "not ever" — most of the entries below are the second kind.

## v1.0 — what ships

Review, fix, approvals, the three surfaces, the CLI, the Hermes plugin. Advisory only: Code-Shipper never
merges and never approves.

## v1.1 — planned

### Approval rules as a required GitHub check

**Status: planned, not built.**

Today the approvals evaluation is posted as a pull request comment. It tells you what is outstanding, and
nothing enforces it. Branch protection can require reviewers; Code-Shipper cannot require its own rule.

v1.1 adds an optional GitHub check whose conclusion is `success` only when the approval rules are met, so
branch protection can require it. The design is already agreed and rests on one rule that v1.0 got wrong
twice before it was right:

> A green check must never read as "ready to merge" when it only means "the review is clear".

Concretely, when this is built:

- `approvals-blocked` concludes `success` — the *review* is clear — but the title must name what is still
  outstanding: `Review clear · approvals still needed (0 of 2)`. Shipped in v1.0 because the wording was
  wrong and a green tick saying "Review complete" invites a merge that the rules forbid.
- An **incomplete** review maps to `action_required`, never `neutral` and never `success`. On GitHub a
  `neutral` conclusion satisfies a required check, so "neutral" for an unfinished review would let a merge
  through — the exact opposite of the rule that incomplete is never a pass.
- **Observe mode writes no check at all.** A check that passes while saying "I did not enforce anything"
  is a check that gets read as enforcement.
- Off by default. A repository opts in.

Why it is not in v1.0: it is a new way to write to GitHub that no real repository has exercised, and the
failure mode is a merge that should not have happened.

## Future (after v1.1) — noted, not scheduled

### PR reading guide
Idea borrowed from [hermes-tern](https://github.com/alt-glitch/hermes-tern)'s `write_review_guide`. On
request ("walk me through #22"), Code-Shipper writes a guide for the human reviewer: a short summary, then
stops in reading order, each with its files, notes and questions to consider. Code-Shipper's open findings
sit at the stop they belong to. The guide records the head commit it was written for and says plainly when
the branch has moved on. Advisory only: no approve, no merge. Shown in the CLI, the TUI and the desktop page.

### Smaller, related
- The Hermes `/ship` plugin remembers which pull requests the current conversation is looking after.
- Optional: findings in a format Tern's `prs` panel can show, so the two tools sit side by side.

## v1.0 — deliberate non-goals

| Not built | Why |
|---|---|
| Merging | The whole point. A human merges. There is no merge method on the broker at all, not a disabled one. |
| Approving | Code-Shipper reports; it does not approve. No verdict is ever `APPROVED`. |
| A Hermes Desktop panel | The desktop surface is the HTML page `ship-page` writes, or the terminal board. A panel inside the Hermes desktop would be a fourth place holding presentation logic. |
| Webhooks | Polling with our own wall clock. A webhook receiver is a service to run and secure. |
| Server-side storage | State lives in a local directory. A service is a different product. |

## Known gaps

- **False-confirm rate unmeasured.** One false confirm in three genuinely clean pull requests is one
  observation, not a rate. The 20-PR run is the measurement.
- **macOS and Windows containment untested.** The unit matrix runs on both, but a real contained fix run
  has only ever happened on Linux.
- **Concurrency at the broker untested under real API load.**
- **A real hosted-provider authentication run** has not been done; the contained harness has only used a
  local model endpoint.
