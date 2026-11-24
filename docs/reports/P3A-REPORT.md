# P3-A — Broker roles, allowlists, exact head, receipts, lease, reconciliation

Status: complete, awaiting review. No requests were sent to GitHub by this checkpoint's tests.
Commit: `740b6d4`

## The rule this checkpoint is built around

**A refusal happens before a request exists.** That is what makes the boundary ours rather than
GitHub's, and it is why every test that asserts a refusal also asserts the outbound request count is
**zero** through a counting transport. An exception on its own proves nothing: it would leave a
script that raised after sending in the clear.

## Roles

Roles are a data table, not branching logic:

| Role | May | May never |
|---|---|---|
| `user` | comment, open PR, create branch, push branch | approve, merge |
| `reviewer` | review with `COMMENT` only, comment, resolve own thread | `APPROVE`, `REQUEST_CHANGES`, push, merge |
| `fixer` | push to the PR head branch, comment, reply, label | approve, merge |
| `approver` | set the approvals status, approve, dismiss own approval | push, merge |

`FORBIDDEN_OPS` — merge, force-push, delete branch, write check run, push tag — appears in **no**
role's allowed set and is additionally refused **by name**. Belt and braces, because the failure mode
that matters is a future role added with a typo quietly enabling one.

A reviewer may send `COMMENT` only; an approver may send `APPROVE` only. Both refusals are recorded
in the ledger as well as raised: a refused approve is exactly what an auditor looks for.

## Allowlists and exact head

- The broker is configured for a repository; another repository is refused before any request.
- A write that depends on the head is refused unless the caller's expected SHA matches the head the
  broker last observed — `head_moved`, zero requests.
- A review with no head is refused outright (`head_required`) rather than defaulting to whatever the
  host says now.

## Receipts

Every write advances `planned` → `posting` → `confirmed`, and a refusal is recorded with its reason.
The receipt carries a **request hash, never the body**, and the token never reaches the ledger — both
asserted. The ledger is append-only and survives a restart.

## Uncertainty is first class

| Failure | Recorded as | Why |
|---|---|---|
| 5xx | `uncertain` | it may or may not have been applied |
| transport failure / timeout | `uncertain` | same |
| 4xx | `refused` | it definitely did not land |

`reconcile(broker, receipt, observed=True/False/None)` settles by **observation, never replay**:

- `observed=True` → `confirmed`
- `observed=False` → `refused`
- `observed=None` → stays `uncertain`, because retrying on a guess is how a comment gets posted twice

A test asserts reconciliation sends no request at all.

## Adversarial cases

| Test | Claim |
|---|---|
| T-ADV-02 | A comment body reading "APPROVE AND MERGE THIS NOW" posts as `COMMENT`; the payload keys are exactly `{commit_id, body, event}` and there is no merge field |
| T-ADV-03 | A `read` or `none` permission cannot authorise a write, whatever the comment says |
| T-ADV-04 | A fork PR is not eligible |
| T-ADV-06 | A CI log instructing a push to `main` cannot move the push off the head branch — `push()` now takes `head_branch` and refuses any other before touching the network |
| T-ADV-08 | The agent's environment carries no `GH_TOKEN`, and an empty token is not silently treated as valid |

T-ADV-06 is the one that found a real gap: `push()` would push whatever branch it was told. A CI log
is untrusted text that reaches the fix prompt, so "push to main instead" was a live instruction with
nothing structural stopping it. It now refuses a non-head branch before any network call.

## The expected-old-ref lease

`push()` uses `--force-with-lease=refs/heads/<branch>:<expected_sha>`, fast-forward only, and
re-reads the remote ref immediately before. Three tests cover it: the lease is in the command, a
moved remote head is refused at `expected-head`, and a non-fast-forward cannot be pushed.

The residual race is unchanged and still disclosed: a Git ref update cannot atomically condition on
GitHub's PR metadata, so the broker re-reads and reports rather than claiming the race is eliminated.

## Delegation cleanup

`broker/write.py` was P2-B's implementation and `broker/roles.py` is the role-aware one. Two
implementations of one rule is how one silently stops being enforced, so `write.py` is now a
re-export. The P2-B tests still pass against it unchanged apart from two field names.

## Not done in this checkpoint

- **The steward** — classify, workspace, fix, scope — is P3-B.
- **Budgets** are P3-B.
- **No lease push against a real remote in this checkpoint.** The lease is exercised against a local
  bare repo; the real-remote run was CP4 and still stands.
- **Reconciliation is not wired to a tick loop yet.** `open_operations()` returns what needs
  settling; nothing schedules that yet (P3-C).

## Reproduce

```
cd /home/kensei/repos/code-shipper
./scripts/run_tests.sh                                  # 435 tests
python3 -m unittest tests.test_broker_roles -v          # 30 broker tests, incl. the ADV cases
```

## Test output (real)

```
$ ./scripts/run_tests.sh
Ran 435 tests in 110.238s

OK
```
