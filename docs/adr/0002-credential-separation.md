# ADR 0002: Enforce authority by credentials, not command matching

Status: Proposed (KENSEI; final form set by the Phase 0 spike)

Context: Claude's local `/ship` blocks merge/approve commands by matching command text, and its
docs admit a command written another way can pass. Hermes `pre_tool_call` hooks see only tool
name and arguments, so a script or `gh api` call can slip past the same way. Owner decision D1:
merging is always human.

Decision: the model process, and any repository code it runs, never holds a GitHub credential.
All writes go through a broker with per-role allowlists, exact-head checks and current-authority
revalidation. The broker's allowlist has no merge operation, and no Code-Shipper surface exposes
merge or auto-merge. In `separated` mode the approver is a distinct identity, and the fixer's token
has no Pull-requests-write scope, so it cannot submit a review that approves.

Consequences: agent approval needs a separate approver identity; the `single` identity mode cannot
enable it. Phase 0 must prove the token setup on real GitHub before Phase 1.

Correction (24/11/2026): an earlier version of this ADR said the fixer's token itself "lacks
approve/merge rights on GitHub". M0 measured otherwise — a Contents-write token CAN merge a pull
request on an unprotected repository, and it merged sandbox PR 2. The boundary is therefore what
the broker and the product will not do, plus branch protection on the target repository, not the
token being incapable. Two cases stay open: GitHub refused a self-approval with 422, and whether
an approval plus a green status then lets the fixer merge is untested. A repository that enables
agent approval needs a ruleset as the last layer (tech spec §6).
