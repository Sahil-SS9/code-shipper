# P3-C — Comment commands, label, watch/stop, and cancellation

Status: complete, awaiting review.
Commits: `5dcd3c8` (commands, cancel, watch, label, config, CLI) + `faee021` (the real cancellation run and the two defects it found)

**Nothing was merged or approved. Every write went through the broker's role allowlist.**

## What was built

| Piece | File | What it does |
|---|---|---|
| Comment commands | `steward/commands.py` | `@code-shipper <instruction>` and `@code-shipper stop`, authority from the host API |
| Cancellation | `steward/cancel.py` | Kills the process group; our wall clock; evidence in the receipt |
| Watch loop | `steward/watch.py` | Rounds, stop conditions checked before each round |
| Contained worker | `steward/contained.py` | The production adapter the loop drives |
| Labels | `broker/roles.py` | `add_label` / `remove_label` with a real DELETE, same role gate |
| Repo config | `config.py` | `read_scope_block`, a stdlib parser that refuses unknown keys |
| CLI | `cli.py` | `watch` and `stop` |

## Authority comes from the host, not the text (T-ADV-03)

`evaluate(comment, permission_of)` asks the host what the author may do. Nothing in the comment is
consulted — not a claim in the body, not the author's name.

| Case | Result |
|---|---|
| `write` / `maintain` / `admin` | acted on |
| `read` | ignored, reason recorded |
| `none` / host failure | ignored |
| Body says "(admin, write access granted)" from a `none` user | **ignored** |

Parsing is literal: a leading `@` is required, so "I asked code-shipper about this" does not steer it,
and `@code-shipper stop the bleeding` is an instruction rather than a stop. A stop outranks an
instruction whatever the order they appear in.

## Stopping (T-SCN-04)

Two independent mechanisms, both checked at the **top** of every round before any work starts:

1. `@code-shipper stop` from a user with write access
2. the `code-shipper-watching` label no longer being present

A stop from a read-only user does not stop it.

## Cancellation (T-ADV-14) — and the two defects the real run found

Unit tests passed against a stand-in worker. The real run found that the stand-in had been hiding
both of these:

### 1. The container was never killed

`run_in_container` used `subprocess.run`, which can only wait. A cancel at 10s was noticed only when a
**41.7s** container exited on its own. Nothing was pushed, but "cancelled" was a lie about *when* it
took effect — and against a harness that ignores its budget, that is the whole point.

Now it runs under the cancel token, killing the process group (SIGTERM, then SIGKILL). Measured:

```
container_seconds_alive: 18.8
kill: {"was_running": true, "exited_after": "sigterm", "returncode": -9}
```

The container died while working, not after finishing.

### 2. A cancellation was reported as a worker failure

`run_fix` caught `Cancelled` in its generic handler and wrote `verdict: worker_failed`, which reads as
a broken harness rather than an instruction. It now has its own verdict at every stage, including the
narrow window after the scope check and before the push — where the push budget is shown untouched.

## The end-to-end cancellation run

Sandbox **PR #24**, branch `p3c-cancel`, head `2bc644d0` before and after.

| Claim | Evidence |
|---|---|
| The harness was killed mid-work | alive 18.8s, `was_running: true`, SIGTERM, rc `-9` |
| No half-pushed state | head identical before and after: `2bc644d0` → `2bc644d0` |
| Nothing pushed | `pushed: false`; no commit on the branch |
| Nothing left behind | `containers_left: 0` |
| **Provider saw nothing after the cancel** | see below — this is the one with a limit |

**The honest limit on the last claim.** When the container is killed, `fix-result.json` is never
written, so there is no authoritative post-cancel call count. The receipt says `calls_after_cancel:
null` with a note, rather than presenting the missing value as `0`. What does prove the point is
structural: the client was dead, so it could not send anything, and the branch is byte-identical. A
cleaner future probe would read the broker's own ledger, which is written host-side and survives the
container — noted, not claimed.

Receipt: `docs/reports/evidence/P3C/cancel-receipt.json`.

## The probe itself had three bugs

Worth recording because each produced a confident wrong answer:

1. A **stale** `adapter-calls.json` from an earlier run in `.staging` made it cancel instantly — it
   read `calls: 3` before the container had started.
2. It read the JSON key `value`; the adapter writes `calls`, so every count came back `0`.
3. It raced a wall clock against container startup. Startup alone takes ~3s and a trivial fix can
   finish before a fixed timer fires. It now waits for the **first model call**, which is
   deterministic.

Two of those runs pushed a real commit to the branch before the cancel landed. Both were reverted and
the branch verified back to `2bc644d0` before the passing run; the diff was checked to confirm no file
was lost.

## Not proven

- **A cancel arriving during an actual model request** (rather than between them). The kill is
  process-level so it should hold, but the receipt does not distinguish the two.
- **The watch loop driven end-to-end against GitHub.** `watch` is unit-tested against a fake host and
  wired into the CLI; it has not run a multi-round session against a live PR.
- **`code-shipper stop` against a live loop** — the CLI path is wired through the broker but not
  exercised end-to-end.
- **T-SCN-01..03** (fix a failing test, resolve a conflict, apply a review comment) remain
  unexercised as full scenarios; T-SCN-01 was effectively covered by P3-B part 2's run, and the other
  two have not been run.

## Reproduce

```
cd ~/repos/code-shipper
./scripts/run_tests.sh                                       # 617 tests
python3 -B spikes/b1/p3c_cancel.py --pr 24 --json            # cancels on the first model call
python3 -m unittest tests.test_p3c -v                        # 34 tests, incl. the kill tests
```

## Test output (real)

```
$ ./scripts/run_tests.sh
...
Ran 617 tests in 120.192s

OK
```
