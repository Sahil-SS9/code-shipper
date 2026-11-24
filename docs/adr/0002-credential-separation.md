# ADR 0002: Enforce authority by credentials, not command matching

Status: Proposed (KENSEI; final form set by the Phase 0 spike)

Context: Claude's local `/ship` blocks merge/approve commands by matching command text, and its
docs admit a command written another way can pass. Hermes `pre_tool_call` hooks see only tool
name and arguments, so a script or `gh api` call can slip past the same way. Owner decision D1:
merging is always human.

Decision: the model process never holds a GitHub credential. All writes go through a broker with
per-role allowlists and exact-head checks. In `separated` mode the fixer's token itself lacks
approve/merge rights on GitHub, and GitHub branch rules are the final layer.

Consequences: agent approval needs a separate approver identity; the `single` identity mode
cannot enable it. Phase 0 must prove the token setup on real GitHub before Phase 1.
