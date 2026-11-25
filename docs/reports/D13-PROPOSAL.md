# D13 proposal — review status as a GitHub check

**Status: proposal only. Nothing is built. Awaiting approval.**

The instruction: *"D13: review status as a GitHub check, through a new, separately tested broker role.
`write_check_run` is in FORBIDDEN_OPS, so propose the role and its limits in the report and stop for
approval before building it."*

This is that proposal. No code for it exists in the tree.

## What the feature is for

Today a reader has to notice a comment or a label to know Code-Shipper has reviewed a PR. A check run
puts the review state where GitHub already aggregates gate state: the PR's checks list, required-status
rules, and the merge box. That is the difference between "a bot commented" and "this pull request has
a review status".

## Why it is not a small change

`write_check_run` is in `FORBIDDEN_OPS`, refused **by name** in `_gate()` before the role check, and
absent from every role's operation set. That was deliberate: a check run is a write that can satisfy a
required-status rule, which means a check is an **approval mechanism in disguise** if the repository
makes it required. Enabling it is a change in the product's threat model, not an added endpoint.

The specific hazard: if a user marks the Code-Shipper check as *required* in branch protection, then a
green check from us gates a merge. A green check must therefore never mean "approved" — only "a review
ran and here is what it found". That distinction has to be structural, not a note in the docs.

## The proposed role

```python
ROLE_OPS["reporter"] = frozenset({"write_check_run", "comment"})
```

A new role, not a widening of an existing one. Reasons:

- **`reviewer` must not gain it.** Reviewer posts COMMENT reviews. If the same role could also write a
  check, a single compromised reviewer path would both comment and gate.
- **`approver` must not gain it.** Approver's job is to approve the work of others. A role that can
  both approve and set the gate is the exact composition the design forbids.
- **A separate role is separately refusable.** `reporter` can be revoked by config without touching the
  review path.

`write_check_run` is removed from `FORBIDDEN_OPS` **only if** every limit below is implemented in the
same commit. Otherwise it stays forbidden and D13 does not ship.

## The limits, each of which I would implement and test

| # | Limit | Why | Test |
|---|---|---|---|
| 1 | **Conclusion vocabulary excludes approval.** Only `success` / `neutral` / `failure` map from review state; there is no `approved` conclusion and no code path that produces one. | A required check must never stand in for human approval. | A test asserting the conclusion for each projection state, and that no state maps to an approval-shaped value. |
| 2 | **The check name is fixed and reserved** (`code-shipper/review`), not caller-supplied. | A caller could otherwise write a check named `ci/tests` or `build` and impersonate another gate. | A test that a caller-supplied name is refused, and that the name is a module constant. |
| 3 | **One check run per head SHA.** Concluded runs are updated, never duplicated. | Duplicate runs on one commit make required-status rules ambiguous and can show a stale green beside a red. | Two calls at one SHA produce one run; a test reads back the id. |
| 4 | **`head_sha` must equal the PR's observed head**, checked through the existing exact-head mechanism. | A check on a SHA that is not the head describes a tree nobody is merging. | A test that a mismatched SHA is refused before any request, with outbound count 0. |
| 5 | **Only the `reporter` role may call it**, and the refusal is recorded. | Same rule as every other operation: a refusal is audit evidence. | A test asserting `reviewer` and `approver` are refused with zero outbound requests. |
| 6 | **`failure` only for a real blocker.** An incomplete review maps to `neutral`, never `failure`. | A `failure` conclusion blocks a merge in a required-check setup. An incomplete review is not evidence of a defect, and must not stop a person merging. | One test per state. |
| 7 | **No `output.title` or `summary` containing untrusted text un-neutralised.** Diff content and finding text go through the existing neutraliser. | A check summary is rendered by GitHub and read by a human; it is the same injection surface as a comment. | The existing framing tests extended to the check payload. |
| 8 | **Observe-only means no check at all**, or a `neutral` check that says so — not a green one. | A green check under observe mode would be read as a pass. | A test that observe mode produces no `success` conclusion. |
| 9 | **The ledger records every check write** with actor, SHA and conclusion. | Same reason as tracked findings: "who set this gate" must be answerable later. | A test reading the ledger after a write. |
| 10 | **Separately tested**, in its own file, exercising the broker role through a counting transport. | The instruction asks for it, and a check write deserves its own attack tests rather than being folded into the review suite. | `tests/test_check_role.py`. |

## What I would not do

- Not make the check **required** by default, and not touch branch protection. Whether the check gates
  a merge stays a repository owner's decision.
- Not let the check carry a **verdict that a machine can act on** beyond its conclusion. The industry-
  standard mistake here is a check whose conclusion is derived from an approval decision.
- Not enable it by default. Off until asked for, like `allow_ship`.

## The decision I need

Three options, in my order of preference:

1. **Approve with all ten limits.** D13 ships as `reporter` + `write_check_run`, removed from
   `FORBIDDEN_OPS` in the same commit as the limits and its own test file.
2. **Approve, but keep `write_check_run` forbidden** and ship D13 as a *comment-only* status (the
   existing in-place approvals comment, extended to carry review state). No new role, no
   `FORBIDDEN_OPS` change. Loses the checks-tab integration; keeps the threat model untouched.
3. **Defer.** Nothing built. The comment already carries the state for a human reader.

My recommendation is **option 1**, because the value of a check is exactly the checks-tab and
required-status integration, and the ten limits address the hazard that makes it risky. But option 2 is
defensible if you would rather never have a Code-Shipper check able to satisfy a required-status rule —
that is the conservative choice, and it costs only the integration.

Until you answer, `write_check_run` stays in `FORBIDDEN_OPS` and nothing in this stop touches it.
