# ADR 0001: Harness-neutral core with adapters

Status: Accepted (owner decisions D3, D4, D7)

Context: Claude Ship works only with Claude; diaktoros only with Hermes. The owner wants one tool
for any agent harness, with Hermes as the first deep integration.

Decision: one core engine with two adapter interfaces: `Harness` (runs an agent task) and
`CodeHost` (reads a code host). Writes go through the broker, never through adapters. Hermes is a
thin plugin over the core; other harnesses call the `code-shipper` CLI.

Consequences: every feature must work through the CLI first; Hermes-only conveniences (slash
command, cron helper) sit in `hermes_plugin.py`. Harness presets stay "unverified" until their
contract test passes.
