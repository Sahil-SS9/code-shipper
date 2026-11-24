# Rootless confinement probe — measured outcome

Owner explicitly reauthorised a disposable synthetic-credential container probe.
Final run: exit 0, twenty checks passed, no stderr. Receipt at
`evidence/M0-rootless/receipt.json`; attempt history and limits at `summary.json` beside it.
Reproducible scripts: `spikes/m0/rootless_probe.py`, `container-probe.sh`.

Used an already cached Caddy Alpine image pinned by local image ID, overriding entrypoint to
/bin/sh. `--pull never`: no downloads. No running services altered. No system/AppArmor/userns
settings changed. The test is a shell confinement probe, NOT a Caddy or Hermes integration.

Benign controls passed: mounted synthetic source readable, visible fake credential readable,
workspace and temporary writes possible. This guards against claiming success from denying
all operations.

Negative/state controls passed: outside fake credential and known host credential paths absent
(no actual secret values read), no Docker/system-bus sockets, source/root filesystem writes
refused, no GitHub/key auth environment, no-new-privileges active, effective capabilities zero,
seccomp filtering active, host process absent, network namespace distinct, no non-loopback
IPv4 route. Network mode is none. This does not test arbitrary syscall exploitation or prove
all possible host-access channels absent.

Initial probe attempts failed because of inherited synthetic-file ACLs and a missing awk path;
fixed the deliberately mounted inputs/receipt permissions and used BusyBox awk. No real
credential file permissions were changed. All disposable containers removed automatically.
Synthetic scratch directories retained for evidence; no broad cleanup performed.

Verdict: Linux rootless Docker is a viable measured route for the basic credential-free worker
container. The original 'system change required before any isolation' blocker is cleared for
this configuration. Full Code-Shipper confinement remains incomplete: no contained Hermes run,
trusted bounded inference bridge, dependency toolchain or broker/cancellation integration was
exercised. In particular a network-denied shell successfully editing files does not prove an
agent can call its model safely. Preserve that next-stage gate before claiming secure fixing.

Next: integrate this result into canonical spec/research matrix, then define and prove scoped
host inference transport without mounting auth directories. Present CLI/TUI/Desktop wireframes
before UI implementation; original builder handoff remains held.
