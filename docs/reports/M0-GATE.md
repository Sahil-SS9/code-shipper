# M0 gate report — Phase 0

Verdict: **READY FOR REVIEW**, with one item open by owner decision (after-approval merge).
Commit: `1fd47aa` (Phase 0 code; this report follows in its own commit).
Phase 0 is complete apart from T-SPIKE-03's after-approval case, which needs the approver token.

## Gate items

### T-SPIKE results

- **T-SPIKE-01 fixer token pushes to a PR branch: VERIFIED.** It pushed `spike/pr-c` in M0, and in
  CP4 the broker pushed `cp4/fix-09316ac0` to PR #16 with a lease. Evidence:
  `docs/reports/evidence/B1/receipt-cp4.json` (`push.ok: true`, `expected_sha`/`new_sha` recorded).
- **T-SPIKE-02 fixer token approves: VERIFIED (refused).** GitHub refuses with 422 where the token's
  account is the PR author, and with a permission error otherwise. Unchanged from M0.
- **T-SPIKE-03 fixer token merges: PARTIAL.** Refused where branch protection requires an approval
  (M0 measured REST and GraphQL merges refused, and direct pushes to `main` refused, while approval
  was missing). The token **can** merge an unprotected PR — it merged sandbox PR 2 — so this asserts
  the protection, not token incapability. **OPEN: approval present + checks green, then the fixer
  merges.** Needs a second GitHub account so someone other than the author can approve. The approver
  token is not yet in place; the test runs as soon as it is.
- **T-SPIKE-04 approver token pushes: NOT DONE.** Same dependency.
- **T-SPIKE-05 agent credentials, contained vs not: VERIFIED for the contained case.**
  Contained: 27/27 canaries pass, every host credential path absent, no docker or system-bus socket,
  no host process visible, no non-loopback route. Uncontained: expected to fail, as documented in
  tech spec §6.1 — a scrubbed environment does not stop an agent reading files by absolute path as
  the same OS user. Evidence: `docs/reports/evidence/B1/receipt-cp3-normal.json`.
- **T-SPIKE-06 contained Hermes plus host inference broker: VERIFIED.** A real `hermes chat` one-shot
  inside the container made real model calls through a per-run capability held by a host broker, and
  returned real text. Two calls, both confirmed by the broker's own ledger. The capability is
  bounded by request count, prompt size, output tokens and expiry. Evidence: `receipt-cp3-normal.json`.

### Containment acceptance (tech spec §6.1)

**VERIFIED** for rootless Docker on this host. Image `hermes-agent-harness:latest` pinned by ID
`sha256:4ffb7a8b30543d148d8ec92bdc8b7ecc50d4a9a051cdba9c15ea8c7189356433`, `--pull never`.

The full mount/env/namespace/network policy is recorded as data in every receipt (`spec` key):

| Property | Value |
|---|---|
| network | `none` (no egress at all; the only channel is a mounted unix socket) |
| root filesystem | read-only |
| user | `65534:65534` (the image defaults to root) |
| capabilities | all dropped |
| no-new-privileges | set |
| tmpfs | `/tmp` (noexec,nosuid) and `/work/hermes-home` (mode 1777, disposable) |
| mounts | `/source` ro, `/work` rw, `/app` ro, `/run/cs` ro — four, all explicit |
| host HOME | not mounted |
| docker / system bus socket | not present |

27 canary checks, all passing, covering both directions: benign controls that must work (source
readable, workspace writable, tmp writable, a deliberately mounted positive-control canary readable)
and negative controls that must not (five host credential paths absent, three synthetic canaries
written outside every mount absent, no credential-shaped environment, no host path in any
environment value, no docker or system-bus socket, host process invisible, root and source not
writable, uid not root, no-new-privileges effective, capabilities zero, network namespace distinct,
no non-loopback route).

The run directory is `0700` and owned by the broker's user, re-verified immediately before the broker
binds. That is what makes the `0o666` socket acceptable: another host user cannot traverse to it.

### Fix loop (the Phase 0 exit requirement)

**VERIFIED.** PR #16 on `Sahil-SS9/code-shipper-sandbox`: broken head `fe6e220e471b`, fixed
`5f0f64a0ee20`. CI failure → success. One line changed in one file, read back from GitHub after the
push. The change was made by Hermes inside the container with the file toolset and no other; the
scope check re-derived the diff from the filesystem on the host rather than trusting the agent's
report; the tests were run before pushing; the push was fast-forward only at the exact expected head.
4 model calls against a ceiling of 40. Nothing merged.

Evidence: `docs/reports/evidence/B1/receipt-cp4.json`, `docs/reports/B1-CP4-REPORT.md`.

### Findings carried into the build

1. **`-t ""` does not disable toolsets.** Hermes treats an empty `--toolsets` value as unset and
   enables every default one, so a session documented as text-only was sending ~20 tool definitions.
   Both workers now disable toolsets explicitly via `hermes tools disable`. Verified in-container: 0
   tools for text-only, file-only for the fix worker.
2. **Hermes treats a broker refusal as a transient outage.** Three retries per attempt, then a
   five-cycle backoff — minutes. Nothing completes, so the boundary holds, but a refused run holds a
   worker. Consequence for the product: cancellation must kill the harness process, not just revoke
   the capability, and wall-clock budgets must be ours rather than inherited.
3. **Push credentials must not travel in argv.** `/proc/<pid>/cmdline` is world-readable without
   hidepid. Fixed in `fixloop.push` by moving the header to `GIT_CONFIG_KEY_0`/`GIT_CONFIG_VALUE_0`,
   with a test that samples `/proc` during a real push. This applies to the product's push code too.

## Deviations from spec (with reason)

- **Transport is a mounted unix socket, not a bridge network or a loopback endpoint** (tech spec
  §2.4 allowed either). With `--network none` there is no route anywhere and no listener to bind, so
  the socket mount is strictly narrower than a dedicated bridge. Recorded in `contain/spec.py`.
- **HERMES_HOME moved to tmpfs** rather than the workspace, because Hermes writes skills, caches,
  sessions and sqlite state; on the bind mount those files were owned by uid 65534 and the host user
  could not delete them. A subdirectory of the tmpfs, because a tmpfs mount point is root-owned and
  cannot be chowned by a non-root process.
- **`MAX_REQUEST_BYTES` is 256 KB, not 64 KB.** A request now carries tool schemas and 20 of them
  exceed 64 KB on their own. Sized from measured traffic (largest prompt 9,312 chars). The bounds
  that carry the security argument are per message and per capability.
- **`hermes tools disable` is used instead of `-t`** for the reason in finding 1.

## Known spike limits (carried to Phase 3, not fixed now)

- **Test-weakening detection is text-based.** It matches `^def test_` at line start, so it misses
  class methods (indented `    def test_`), unittest-style `self.assert*` without a bare `assert`,
  and `assert True` padding. The product needs an AST-based check.
- **Allowed and forbidden paths are hard-coded** for the sandbox (`calc/**`, `tests/**`, and the
  forbidden list). The product takes these from the policy file.
- **One probe check is stale rather than wrong.** The external CP1 probe sends a 70 KB body expecting
  `request_too_large`; at the correct 256 KB limit that is refused as `unknown_field` instead. The
  owner updated the probe independently; the oversize path is covered by four unit tests at the real
  limit including a multi-megabyte body.

## Open risks

- **T-SPIKE-03 after-approval merge is unresolved.** Until it runs, the safe assumption is that a
  Contents-write token can merge once an approval exists, so agent approval must require a ruleset
  that restricts merging to listed humans. The README already says so.
- **Only local inference has been proven.** All real calls went to Turbofit on loopback with a
  synthetic credential. Hosted-provider authentication through the broker is untested.
- **Containment is not fixing safety.** A shell that cannot see credentials is not an agent that
  produces a correct, in-scope diff. The one-character fix is evidence for the mechanism, not for
  review quality.
- **No dependency toolchain inside the container.** The fix needed only the standard library.
- **Cancellation of an in-flight request is unproven** — proven that no *new* completion happens
  after the broker stops.
- **Concurrency, macOS and Windows are untouched.**

## Owner questions

1. **The approver token.** `drop-secret code-shipper-approver` once the throwaway account exists with
   write access to `code-shipper-sandbox`. This closes T-SPIKE-03 and T-SPIKE-04.
2. **One open decision was answered:** the `0o666` socket is accepted, with the `0700` run directory
   as the enforcing control.

## Plain-English summary

Phase 0 is done except one test that needs a second GitHub account. A contained agent now really does
fix a pull request: it edited the file inside the sealed container, the host checked the diff and ran
the tests, and the broker pushed it. CI went from red to green and nothing was merged. The container
cannot see any credential, and when the model broker is switched off nothing gets an answer — proven
by 7 refused attempts rather than assumed. Three things to carry forward: an empty toolsets flag
enables every tool, a refused model call makes Hermes retry for minutes, and the push token was
visible in the process list until this round fixed it.
