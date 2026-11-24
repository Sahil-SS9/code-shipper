# B1 — CP4: one real fix, end to end, inside the container

Status: CP4 complete, awaiting review. This is the last Phase 0 item.
Verdict: PASS.

## The claim

A contained Hermes session, with file-edit tools and no network, fixes a failing test in a real
pull request; the host broker re-derives the change, checks it is in scope, runs the tests and pushes
it fast-forward only at the exact expected head; CI goes green; nothing is merged.

Measured. Evidence below, and the limits stated at the end.

## Commits

| Commit | Contents |
|--------|----------|
| `58e332d` | Mid-run refusal evidence (the gap from the CP3 review) |
| `e45aace` | Tool calls end to end; CP4 fix loop |

Local only, unpushed, authored as the repository's configured identity.

## The run

| | |
|---|---|
| PR | #13 on `Sahil-SS9/code-shipper-sandbox` |
| Branch | `cp4/fix-15fabd8c` |
| Broken head | `79c84b6dafa6` |
| Fixed head | `c85ae5c3deb6` |
| CI before | failure |
| CI after | success |
| Canaries | 27/27 in the pinned Hermes image |
| Model calls | 6 against a ceiling of 40 |
| Merged | no |

The diff, read back from GitHub after the push:

```
--- a/calc/__init__.py
+++ b/calc/__init__.py
@@ -5,4 +5,4 @@ def add(a, b):
 def divide(a, b):
     if b == 0:
         raise ZeroDivisionError("b must not be zero")
-    return a * b  # deliberate bug
+    return a / b
```

One character of real work. That is the point: the mechanism is what is being proven, not the
difficulty of the bug.

## How the boundary was held

**Inside the container.** Same spec as CP3: `--network none`, read-only root, uid 65534, all
capabilities dropped, no-new-privileges, disposable tmpfs `HERMES_HOME`, and exactly four mounts
(`/source` ro, `/work` rw, `/app` ro, `/run/cs` ro). The worker gets a copy of the repo with **no
`.git` directory**, so it cannot commit, branch or push even if it tries. All 27 canaries passed
inside that container before Hermes was allowed to start.

**The credential.** The only key is in the broker, on the host, read from a file. Hermes is
configured with a placeholder that the adapter discards. The fixer token is read by path, used for
one `git push` invocation as an `-c http.extraheader`, and never printed, written or logged.

**The push.** `--force-with-lease=refs/heads/<branch>:<expected_sha>`, fast-forward only, hooks
disabled via `core.hooksPath`, and the remote head is re-read and compared to the expected SHA
immediately before. If the branch had moved, the push would have been refused.

**Scope, re-derived on the host.** The worker's own report of what it changed is not trusted.
`check_scope` hashes the tree before and after and judges the difference:

- only `calc/**/*.py` and `tests/**/*.py` are writable paths
- deleting files is refused
- more than 10 files is refused
- more than 200 KB is refused
- `.github/`, `pyproject.toml`, `setup.cfg`, `conftest.py` and `.code-shipper/` are refused outright
- a change to a test may not remove a test function, add a skip/xfail marker, or reduce the number of
  assertions

That last rule is the important one. Containment stops an agent reaching credentials or the network;
it does not stop it rewriting a test so CI goes green without fixing anything. Fourteen unit tests
cover these rules in both directions — real fixes accepted, each cheat refused — including that the
test-weakening check fires on its own.

## What went wrong on the way, and why it matters

Three defects were only found by running this for real. Each is the kind that reading the code
would not have caught.

**1. Tool calls were dropped at every layer.** Hermes sent four file tools, my adapter stripped them,
the broker refused the `tool` role, and the provider therefore answered with text like "Let me look
at the source" and never edited anything. The run looked like the agent had declined. Fixed through
the whole path: broker, upstream client and adapter now carry tool definitions, tool calls, and the
OpenAI message shapes a tool round-trip needs (assistant `tool_calls` with null content, tool results
with `tool_call_id`). Content parts are still refused — this channel is text and tool calls.

**2. The workspace was not writable by the worker.** The repo copy was owned by the host user and the
container runs as 65534, so every edit failed with `Permission denied`. To Hermes's credit it
reported that honestly rather than claiming success: its answer was `{"status": "no_change",
"summary": "...every edit attempt failed with 'Permission denied'..."}`. That is the behaviour we
want from a harness, and it is worth noting because a less honest one would have produced a
plausible summary with no diff.

**3. git refused the worktree** because the fixed files belonged to another uid. `chown` needs root,
which this task does not have. The fix is `-c safe.directory=<path>` on each git invocation, which
git honours where repository-local config is deliberately ignored for that setting — narrower than a
global entry and needing no privileges.

## Bounds, as required

- our own wall clock: `run_hermes` uses `Popen` with our timeout and kills the process itself. In the
  mid-run refusal case it took 60 seconds to stop a session Hermes would have kept retrying.
- broker capability: 40 requests, 120k prompt chars, 2048 output tokens. Actual: 6 calls.
- one fix round, one push, no merge.

## Still unproven

- **Real hosted-provider authentication.** Still Turbofit with a synthetic key. A billable provider
  through this path is untested.
- **Anything harder than a one-character fix.** No multi-file change, no dependency install, no test
  suite that needs a toolchain inside the container.
- **The scope rules are heuristics.** They catch deletion, skipping and assertion removal in the test
  files, and they constrain paths and size. They cannot tell whether a source fix is correct; that is
  what CI and the tests are for, and they passed here.
- **Cancellation of an in-flight request.** Proven: no new completion after the broker stops. Not
  proven: a request already in the socket when the broker dies.
- **Concurrency** — one run at a time.
- **macOS and Windows backends** — untouched.
- The mid-run "did it really try" gap from the CP3 review is now closed and separately evidenced:
  7 refused attempts recorded, 0 completions, our own timeout killing the run.

## Reproduce

```
cd /home/kensei/repos/code-shipper

./scripts/run_tests.sh                 # 161 unit tests

# the full CP4 loop: creates a branch and PR on the sandbox, fixes it, pushes, waits for CI
python3 -B spikes/b1/cp4_run.py        # ~5 minutes; --json for the receipt
```

It needs: the rootless Docker context, the cached Hermes image, Turbofit on 8091, `gh` logged in,
and the fixer token at `~/.config/code-shipper/tokens/fixer`. Nothing is billed and nothing leaves
the machine.

## Test output (real)

```
$ ./scripts/run_tests.sh
----------------------------------------------------------------------
Ran 161 tests in 22.783s

OK
```

Receipt: `docs/reports/evidence/B1/receipt-cp4.json`.
