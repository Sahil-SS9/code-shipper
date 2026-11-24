# ADR 0003: Own approval policy format at `.code-shipper/approvals.yml`

Status: Proposed (KENSEI default K2)

Context: Claude Approvals uses `.claude/approvals.yml`. Reusing the path or schema would imply
compatibility we cannot guarantee and could collide in repos that use both.

Decision: own file and schema, similar concepts (paths, condition, seats, agent verdict,
strictest wins, protected files, observe/enforce), clean-room from public docs. An importer from
the Claude format may come later.

Consequences: users of both tools keep two files; documentation must show the mapping.
