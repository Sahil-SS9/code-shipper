# ADR 0005 — No pre-merge custom checks in v1

Status: accepted (owner decision D14, 24/11/2026).
Supersedes nothing. Related: ADR 0002 (authority from credentials, not text), tech spec §7.

## Context

The review-modes research (C14) proposed a feature we could have shipped: user-defined pre-merge
checks whose result is evaluated before a PR can merge — "this PR must not introduce a new
`SELECT *`", or "every new endpoint must have a test". It is a natural thing to want, and it looks
like it belongs next to the approval policy.

Two things argued against it.

**It is model judgement on author-controlled text.** A custom check is a natural-language rule
evaluated against a diff. The rule is written by a maintainer, but the thing it is evaluated against
is written by the PR author, who can phrase code and comments to satisfy it. That is the same
adversarial surface as review framing (tech spec §7), where the conclusion was already that framing
is a mitigation rather than a boundary. A check whose verdict gates a merge cannot be a mitigation.

**It overlaps the approval policy.** The policy engine already expresses who must approve, under what
conditions, per path. A second, fuzzier mechanism that can also block a merge gives two sources of
truth for the same question, and the weaker one is the one that surprises people.

## Decision

Code-Shipper does not implement pre-merge custom checks in v1. `receipts` and review findings are
**advisory**: they inform a human, and the approval policy decides. There is no path by which a model
verdict blocks a merge.

Recorded as an ADR rather than left unsaid, so a future contributor proposing the feature finds the
reasoning rather than rediscovering the problem.

## Consequences

- Users who want custom merge conditions express them in `approvals.yml` (paths, counts, conditions)
  or in their CI, where the check is deterministic code rather than a model's reading of prose.
- We give up a feature that would have looked impressive in a comparison table.
- The boundary stays simple to explain: **credentials decide authority, humans merge, and nothing a
  model says can block a merge by itself.**
