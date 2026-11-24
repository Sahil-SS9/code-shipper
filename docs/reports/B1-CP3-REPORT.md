# B1 — CP3: one real model call from inside the container

Status: CP3 complete, awaiting review. CP1 and CP2 reviewed and approved.
Verdict: PASS for what CP3 claims.

## The claim, and only the claim

A real Hermes Agent session runs inside the measured rootless container and completes a model call
through a bounded host broker, with no credential material reachable from inside, and with no model
available when that broker is stopped.

Measured. What it does **not** prove is listed under "Still unproven" and should be read before
anyone describes contained fixing as safe.

## Commits

| Commit | Contents |
|--------|----------|
| `0227a63` | Run-directory permission guard; in-container loopback adapter |
| `44f8941` | SSE streaming; disposable HERMES_HOME; file-based call counter |
| `e8c83d7` | CP3 harness |

Local only, unpushed, authored as the repository's configured identity.

## Condition 1 — the socket, and what actually protects it

The broker socket is `0o666`. That is not the control. **The control is the run directory.**

- `mkdtemp` creates each run directory `0700`, owned by the broker's user.
- `require_private_run_dir` now enforces that: it refuses any path whose mode is not exactly
  `0700`, or whose owner is not the current user. Setuid, setgid and sticky bits are rejected too.
- It is called twice: when the directory is made, and again in the harness **immediately before the
  broker binds a socket inside it**. The second call is what catches a widening in between — a test
  covers exactly that sequence.
- Measured in every CP3 receipt: `run dir 0o700 owned 1001, socket 0o666`.

Another host user cannot traverse a `0700` directory, so it cannot reach the socket inside it
whatever the socket's own mode says. The only account that can is the one that already holds the
model key.

**`CS_STAGING_ROOT` cannot weaken this.** It moves only the *parent* directory. The guard applies to
the run directory created inside it, so the per-run guarantee is unchanged. A parent that is group-
or world-writable is refused outright rather than trusted, because another user could otherwise
plant or rename a run directory. Tests cover: the env var moving the parent, the run directory
staying `0700` under a custom parent, and world-writable and group-writable parents being rejected.

## Condition 2 — the image

`hermes-agent-harness:latest`, pinned by ID `sha256:4ffb7a8b30543d148d8ec92bdc8b7ecc50d4a9a051cdba9c15ea8c7189356433`,
run with `--pull never`. No downloads.

The image defaults to `root`; the container runs as `65534:65534` anyway, with all capabilities
dropped and `no-new-privileges`. Verified `uid is not root [uid=65534]` and `capabilities empty`.

**HERMES_HOME is never the image's `/root/.hermes`.** It is a fresh directory inside a `tmpfs`
mounted at `/work/hermes-home`, mode `1777`, so the non-root worker can create its own home inside
it. It is a subdirectory rather than the mount point because the mount point is root-owned and a
non-root process cannot chown it — and Hermes refuses a control path it does not own. Every run
starts empty. Nothing persists.

Why tmpfs rather than the bind-mounted workspace: I hit this the hard way. With HERMES_HOME on the
bind mount, Hermes wrote skills, caches, sessions and sqlite state as uid 65534, and the host user
then could not delete any of it. A tmpfs is disposable and leaves the host clean.

## Condition 3 — the model path

Hermes speaks OpenAI chat-completions. The broker speaks one JSON line over a unix socket. Between
them, inside the container, runs `adapter.py` on `127.0.0.1:8111`.

- It listens on loopback **inside** the container's own network namespace. With `--network none`
  there is no route anywhere else, so the adapter is the only reachable endpoint and the broker
  socket is the only thing the adapter can reach.
- The API key Hermes is configured with is a literal placeholder. The adapter discards the
  `Authorization` header rather than forwarding it — asserted by a test that inspects what reached
  the broker.
- The real key exists only in the broker, on the host, read from a file (never an environment
  variable, so it cannot be inherited by a child or shown by `docker inspect`).

Two defects surfaced only by running Hermes for real, both now fixed with tests:

1. **The adapter answered a streaming request with a plain JSON body.** Hermes uses the OpenAI
   client with `stream=True`, could not parse the response, and retried until it hung. It now emits
   `chat.completion.chunk` SSE events.
2. **The call counter was a module global**, so it read zero across the worker/adapter process
   boundary. It is now a file incremented under a `flock` per request. Only requests the broker
   actually served are counted, so a refused request is not a model call.

## Condition 4 — the upstream

Local Turbofit at `http://127.0.0.1:8091`, model `auto`. Nothing leaves the machine and nothing is
billed. The broker's credential file holds a synthetic value (`sk-FAKE-cp3-synthetic-key-0000`);
Turbofit does not check it.

**Real hosted-provider authentication is unproven.** CP3 proves the transport, the capability
boundary and the credential separation. It does not prove that a real hosted provider (OpenAI,
Anthropic, anything with a billable key) authenticates correctly through this path. That is a
separate test, and it needs a real key the owner supplies.

## Condition 5 — bounding

- Tiny prompt: `Reply with exactly one word: pong. No punctuation, no explanation.`
- `-t ""` — no toolsets enabled.
- `--max-turns 2 --run-budget 120`.
- Broker capability: `max_requests: 8`, `max_prompt_chars: 64000`, `max_output_tokens: 1024`.

**Hermes made 2 adapter calls and the broker confirmed 2 completions.** Two, not one, is the honest
number: Hermes issues a streaming request and then a non-streaming "unmask probe", as its own logs
show. The ceiling of 8 was never approached, and the receipt records both numbers so the margin is
visible rather than implied.

## Condition 6 — canaries and fail-closed, in the Hermes image

The full canary set was re-run inside `hermes-agent-harness:latest`, which differs from the CP2
image. 27 checks, all passing:

- benign: source read-only and readable, source content readable, workspace writable, tmp writable,
  deliberately-mounted positive-control canary readable
- credential paths absent (existence checks only, never reads): `~/.hermes/.env`,
  `~/.config/gh/hosts.yml`, `~/.git-credentials`, `~/.ssh/id_rsa`,
  `~/.config/code-shipper/tokens/fixer`
- three synthetic canaries written **outside every mount** are absent
- no credential-shaped environment variables; no host path in any environment value
- no docker socket; no system bus socket; host process invisible in `/proc`
- root filesystem not writable; source not writable
- uid is not root; no-new-privileges effective; effective capabilities zero
- network namespace differs from the host; no non-loopback route
- broker socket present; no credential file beside the socket

I also ran a separate ad-hoc check outside the harness, straight `docker run` with only a
read-only mount of the staging tree: all six host credential paths absent, `0` credential-shaped
environment variables, `0` routes.

### Fail-closed, mode 1: broker stopped before the run

`run rc 1` in 0.39s. Canaries: 26 passed, 1 failed — `broker socket present`, because the socket
genuinely is not there. `adapter calls 0`, `completions 0`, no Hermes output. The socket is absent,
so no completion is possible.

### Fail-closed, mode 2: broker stopped mid-run

`run rc 1` after 205.9s. All 27 canaries passed (the socket existed when the run started and the
network policy was unchanged). `adapter calls 0`, `completions 0`, no Hermes output.

This one is the strongest of the three. Hermes was already running with a live adapter, the broker
died, and the only outcome was 206 seconds of retries ending in a non-zero exit with zero
completions. Getting this right took a fix: my first attempt passed a delay the worker ignored, so
the run finished before the broker was stopped and the test proved nothing. The delay hook is now in
the Hermes worker and the receipt records a 205-second run, which is what a real refusal looks like.

## A finding worth carrying into the product

When the broker refuses, Hermes does not fail fast. It retries three times per attempt and then
enters a five-cycle recovery loop with backoff — 32s, then 66s, and so on. Nothing is completed, so
the boundary holds, but a refused run occupies a worker for minutes.

Two consequences for the real implementation:

- The broker's `stop`/`cancel` path must be able to kill the harness process, not just revoke the
  capability. Revocation stops the next call; it does not stop a retry loop.
- Per-run wall-clock budgets have to be enforced by us, not inherited from the harness, because the
  harness treats a refusal as a transient provider outage.

## Still unproven

- **Real hosted-provider authentication.** Turbofit ignores the key. A billable provider through
  this path is untested.
- **A real fix.** CP3 calls the model once with a one-word prompt and no tools. It does not review a
  diff, edit a file, or push anything. Containment is not fixing safety.
- **A dependency toolchain inside the worker.** Nothing installs or runs a build or test suite.
- **Cancellation before an in-flight request lands.** Proven: no new completion after the broker
  stops. Not proven: a request already in the socket when the broker dies.
- **Long sessions and concurrency.** One session, one run at a time.
- **macOS and Windows backends.** Untouched.
- The capability token still sits in the shared workspace, and the placeholder key is still written
  into the container's HERMES_HOME `.env`. Both are inert — the token is a per-run permission and
  the placeholder authorises nothing — but neither is a secret.

## Reproduce

```
cd /home/kensei/repos/code-shipper

# unit tests
./scripts/run_tests.sh

# CP3: one real model call through the broker
python3 -B spikes/b1/cp3_run.py

# CP3: fail closed, broker stopped before the run
python3 -B spikes/b1/cp3_run.py --stop-broker-before-run

# CP3: fail closed, broker stopped mid-run (takes ~3.5 minutes)
python3 -B spikes/b1/cp3_run.py --stop-broker-mid-run
```

Each prints a summary and exits 0 only when its verdict passes. `--json` prints the full receipt;
`--keep` retains the staging tree.

## Test output (real)

```
$ ./scripts/run_tests.sh
----------------------------------------------------------------------
Ran 122 tests in 21.256s

OK
```

Receipts: `docs/reports/evidence/B1/receipt-cp3-{normal,before,mid}.json`.
