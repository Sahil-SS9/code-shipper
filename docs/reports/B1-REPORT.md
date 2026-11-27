# B1 — contained worker, host inference broker, stand-in agent

Status: CP2 complete. CP1 complete and reviewed. CP3 not started.
Verdict: PASS for what CP2 claims. Read "Still unproven" before treating anything here as safe
fixing.

## What this checkpoint answers, and what it does not

Answers: can a worker inside the measured rootless container reach exactly one thing outside it —
a per-run model capability held by a host process — while a canary set confirms that no credential
path, host socket, host process or network route is reachable, and while the run fails if that one
channel does not exist?

Does not answer: whether a real Hermes session runs in there and completes a review or a fix. CP2
uses a stand-in agent on purpose. That is CP3.

## Checkpoints

| CP | Claim | State |
|----|-------|-------|
| CP1 | Broker serves only bounded model requests and refuses everything if it fails | Committed, independently reviewed (two findings fixed, see below) |
| CP2 | Container spec plus canary set pass with a stand-in agent through the broker | This report |
| CP3 | One real model call from inside the container; no credentials reachable; no model when the broker is stopped | Not started |

## Commits

| Commit | Contents |
|--------|----------|
| `74af828` | Broker, capabilities, protocol validation, client (38 tests) |
| `136303a` | Broker entry point that refuses to start half-configured |
| `0cfdd55` | CP1 review finding 1 (redirect key leak) and finding 2 (socket mode default) |
| `ed8ad8b` | CP1 review nits: oversize refusal, slot accounting, socket-path safety, revoke race |
| `6515039` | Container runtime spec and contained worker entry point |
| `71161ae` | CP2 harness and canary set |

All local, unpushed. Author on every commit is the repository's configured identity.

## CP1 review findings, both fixed with regression tests

1. **Redirect key leak (confirmed real).** `urllib` follows a 3xx and resends the original headers,
   including `Authorization`, to the redirect target. Reproduced directly: a 302 to a second
   loopback server delivered `Authorization: Bearer sk-FAKE...` and the broker reported `ok: true`.
   Fixed with a redirect handler that raises, so any 3xx becomes `upstream_error`. The test asserts
   the redirect target is *never contacted*, not merely that the broker returned an error.
2. **`serve_socket` defaulted to `0o666`.** The library default is now `0o600`; widening for the
   container is explicit at the call site (and is what CP2 does, see "Deliberate weaknesses").

Both tests were run against the unfixed code first and failed.

Four optional nits were also taken, each proven the same way (red without fix, green with):

- Oversized requests could lose their refusal to a connection reset. The first attempt at this
  introduced a worse bug: a byte-capped drain reproduced the reset at 2 MB. Drain is now
  time-bounded. Verified at 69 KB, 2 MB and 8 MB.
- The per-connection semaphore release sat in an inner `try/finally` a rewrite could drop; the
  served counter used a lock object created and discarded per call.
- `start()` unlinked anything at the socket path. It now removes a stale *socket* only and refuses a
  regular file or symlink.
- Revoked and expiry were read outside the lock, so a revoke could land between the check and the
  reservation. Both are re-checked inside the reserving critical section.

## Design decision: transport

The worker gets **no network at all** (`--network none`). It reaches the broker through a unix
socket in a read-only bind mount. The alternative in the handoff — a dedicated bridge with only the
broker reachable — would have needed an IP endpoint, a listener bound to it, and an egress rule
that is correct. A socket mount needs none of those and the worker still has no route anywhere.
Verified by `PASS no non-loopback route` and `PASS network namespace is not the host's`.

## The spec, as measured

From `evidence/B1/receipt-normal.json`:

```
network            none
root_filesystem    read-only
user               65534:65534
capabilities       ALL dropped
no_new_privileges  true
pids_limit         64
memory             512m
cpus               1
tmpfs              /tmp:rw,noexec,nosuid,size=64m
mounts             /source ro, /work rw, /app ro, /run/cs ro
host_home          none
docker_socket      none
image              sha256:9a0a6ac529b67b004461d4f7753a4f9f331a49c52e44b89aa7268e8f8709cfda
```

Image is `slopsearx-kensei:local`, already cached, used with `--pull never`. No downloads.

## Canary results

27 checks, from `evidence/B1/receipt-normal.json`.

Benign controls — these exist so "everything denied" cannot masquerade as confinement:

- source mounted read-only and readable; source file content readable
- workspace writable; /tmp writable
- positive control: a canary deliberately mounted at `/source/visible-canary.txt` is readable

Negative controls:

- absent: `~/.hermes/.env`, `~/.config/gh/hosts.yml`, `~/.git-credentials`, `~/.ssh/id_rsa`,
  `~/.config/code-shipper/tokens/fixer` (path checks only; no real secret was opened)
- absent: three synthetic canary files the host wrote *outside every mount* — reachable only if a
  mount were larger than intended
- no credential-shaped environment variables; no host path in any environment value
- no docker socket; no system bus socket; host process not visible in `/proc`
- root filesystem not writable; source not writable
- uid is not root (65534); no-new-privileges effective; effective capabilities zero
- network namespace differs from the host; no non-loopback route
- broker socket present; no credential file beside the socket

## End-to-end result

The stand-in worker made one call through the broker and got real text back:

```
worker result  {"text": "pong", "usage": {...}, "uid": 65534}
```

The loopback stand-in provider recorded exactly one hit, with a bearer header. So the credential
reached the provider and never entered the container.

Broker receipt (hashes and counts only — no prompt text, no completion text):

```
{"outcome": "confirmed", "run_id": "cp2-47fb924c", "provider": "local",
 "model": "auto", "prompt_chars": 32, "completion_chars": 4, "error": null}
```

## Fail-closed proofs

**Broker stopped before the run** (`--stop-broker-before-run`): run exits 1, no result file, the
provider is never contacted, and the canary set reports exactly one failure — `broker socket
present` — because the socket really is gone. No completion is possible.

**Broker stopped mid-run** (`--stop-broker-mid-run`): the run starts with the socket present, all
27 canaries pass, then the broker dies before the worker's call. The worker exits 2 with
`worker: broker unreachable`, writes no result, and the provider records zero hits.

Both are reproduced from one command each; see below.

## Deliberate weaknesses, stated plainly

These are choices, not oversights, and each is a real limitation.

1. **The broker socket is mode `0o666`.** The worker runs as uid 65534 inside the container's user
   namespace; the broker runs as uid 1001 on the host. A `0660` socket would need a group both
   share, which is a system change this task forbids. Consequence: any process that can reach that
   directory can talk to the broker. The compensating controls are that the directory holds only
   the socket, the socket is removed when the broker stops, and a capability is scoped to one run,
   one model and a request count.
2. **The capability token sits in the shared workspace.** The worker must read it, so it is not a
   secret from the worker — it is a bounded permission that dies with the run. It is not a
   credential: it authenticates one run to one model and cannot be used anywhere else.
3. **Host facts are passed in a JSON file**, including the host's `$HOME` path and the outside
   canary paths. This tells the worker where to look and confirm absence. It does not grant access;
   the mounts are what grant access. Accepted because otherwise the negative controls would be
   indistinguishable from a broken probe.
4. **`--keep` leaves the staging tree on disk.** Only used to capture the receipts above; the
   default removes it, and `discard()` refuses any path outside the staging root.

## Still unproven

- A real Hermes session inside this container. The stand-in does not load a harness, does not use
  tools, and does not have the footprint of `hermes chat`. CP3.
- A dependency toolchain inside the worker (the stand-in needs only the standard library).
- Cancellation: there is no proof that stopping a run revokes the capability *before* an in-flight
  request lands, only that stopping the broker refuses new ones.
- Long-session state, concurrent runs, and multi-run isolation are not exercised.
- Containment is not fixing safety. A shell that cannot see credentials is not the same as an agent
  that produces a correct, in-scope diff.
- Windows and macOS backends are untouched.

## Reproduce

```
cd ~/repos/code-shipper

# unit tests
python3 -B -m unittest discover -s tests -t .

# CP1 review probe (independent)
python3 ~/.hermes/cache/scratch/cs-cp1-probe.py

# CP2: normal run
python3 -B spikes/b1/cp2_run.py

# CP2: fail closed, broker stopped first
python3 -B spikes/b1/cp2_run.py --stop-broker-before-run

# CP2: fail closed, broker stopped mid-run
python3 -B spikes/b1/cp2_run.py --stop-broker-mid-run
```

Each prints a summary and exits 0 only when its verdict passes. `--json` prints the full receipt;
`--keep` retains the staging tree.

## Test output (real)

```
$ python3 -B -m unittest discover -s tests -t .
.....................................................................................
----------------------------------------------------------------------
Ran 85 tests in 12.248s

OK
```

CP1 review probe:

```
TOTAL 38  PASS 38  FAIL 0
```

Canary counts: 27/27 (normal and mid-run), 26/27 with the one expected failure (before-run).
