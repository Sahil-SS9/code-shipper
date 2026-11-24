# ADR 0004: Python standard library only at runtime

Status: Proposed (KENSEI default K1)

Context: Hermes plugins are Python and install from a git repo; a pip dependency step inside
Hermes adds failure modes. The CLI must also install cleanly with pipx/uv on Linux, macOS and
Windows.

Decision: runtime uses only the standard library (urllib, subprocess, json, tomllib). The policy
parser is a strict in-repo YAML subset; inside Hermes it may use the public `hermes_yaml` loader
with identical validation (parity test).

Consequences: more code to own (HTTP retries, YAML subset); fewer supply-chain risks.
