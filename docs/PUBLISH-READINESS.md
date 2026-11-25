# Publish-readiness checklist

A gate list, not a summary. Every line is either **met** (with evidence), **not met**, or **not
applicable yet**. "Mostly" is not an option: a checklist that can be half-satisfied is a checklist that
will be.

Last updated at the end of the final back-end stop.

## 1. Product constraints that cannot regress

| Requirement | Status | Evidence |
|---|---|---|
| No surface offers a merge action | **Met** | `ALLOWED_ACTIONS` has no merge member; AST test over every surface module; `merge` appears in no broker role and is refused by name in `_gate()` |
| The tool never approves its own work | **Met** | `REVIEW_EVENTS_BY_ROLE` restricts reviewer to `COMMENT`; approver is a separate role with no review path |
| A refusal happens before any request | **Met** | Every refusal test asserts outbound count is zero |
| The model process never holds a GitHub token | **Met** | Token path read by the host-side CLI only; container gets a placeholder; `test_push_credentials` asserts no credential reaches the mountable surface |
| One implementation of one rule | **Met** | `broker/write.py` re-exports `broker/roles.py`; `fixloop` delegates scope to `steward/policy`; surfaces hold no logic |
| Every write is ledgered with actor and timestamp | **Met** | Broker ledger states; tracked-findings ledger; approvals comment id recorded |

## 2. Behaviour under failure

| Requirement | Status | Evidence |
|---|---|---|
| An incomplete review is never reported as clean | **Met** | `complete=False` → state `incomplete`, verdict withheld; test per surface |
| An exhausted budget can still report | **Met** | C2 reserve: main work refused at `wall_clock - wrap_up`, one-shot wrap-up; test with the main budget at zero |
| A rewritten history forces a full review | **Met** | D5 ancestry guard: `diverged`/`behind`/empty-compare/unknown all fall back, with the reason in the body |
| An empty incremental scan is not "nothing to do" | **Met** | The old `scope.empty → complete=True` path is removed; the test that asserted it now asserts the fallback |
| An uncertain write is settled by observation, never replayed | **Met** | `reconcile()`; a test asserts reconciliation sends no request |
| A force-push, rebase or interrupted push leaves no half-state | **Met** | `--force-with-lease` at the exact expected head; the lease is re-read before pushing; cancellation proof: `pushed: false`, `branch unchanged: true` |
| A cancellation kills the run mid-flight | **Met** | `kill: {was_running: true, signal: SIGTERM}`, `rc: -9`, 18.8s of a 45-minute allowance |

## 3. Honesty of output

| Requirement | Status | Evidence |
|---|---|---|
| The README says polling is "once every N seconds", never "continuous" | **Met** | README "Polling, precisely" section |
| Spend that is not metered reads as unknown, never `$0` | **Met** | `Budget.spend_text()`; surface test |
| A stale verdict is labelled | **Met** | `head_pinned`; both surfaces render STALE |
| A merged-away finding is named, not silently dropped | **Met** | `counts.merged_away`, "Also noted on this line: …" |
| A tracked finding's satisfaction says "evidence, not proof" | **Met** | `reply_body()` footer; test asserts the phrase |
| The false-confirm rate is measured, not asserted | **See §6** | Measured on seeded clean PRs |

## 4. Security

| Requirement | Status | Evidence |
|---|---|---|
| Untrusted text cannot frame an instruction | **Met as a mitigation, not a boundary** | Shared neutraliser for description, diff, CI log and finding text; 13 tag variants × 4 block types |
| Boundaries are commented in the tech spec | **Met** | Tech spec §7 names what actually holds: COMMENT-only posting, schema-validated output, host-side scope and budget checks |
| Secrets are scanned before publication | **Met** | `secrets.py`; a finding blocks; redaction applied to all emitted text |
| Publishing requires a recorded authorisation | **Met** | Non-TTY run without it exits 2 with a message; measured, nothing committed |
| Credential-looking paths cannot be mounted | **Met** | Guard covers `/` and `\` separators (a Windows path was mountable before that fix) |
| A check run cannot impersonate another gate | **Not built** | D13 is a proposal awaiting approval; `write_check_run` remains forbidden |

## 5. Independent verification

| Requirement | Status |
|---|---|
| Every checkpoint has a CI run on the exact commit, matrix green | **Met** — see the per-phase reports for run IDs |
| Tests are stdlib `unittest`, no pytest dependency | **Met** |
| The suite runs from a bare `./scripts/run_tests.sh` | **Met** |
| Every commit authored as the configured identity | **Met** |

## 6. Measured false-confirm rate

The gate in `docs/prd.md` asks for a precision measurement before public release. What has been measured
and what has not:

Five seeded pull requests (PRs #26–#30 on the sandbox) were reviewed end to end with the real harness.
Adjudicating each finding against the code, by hand and after the fact:

| PR | Seed | Findings | Adjudication |
|---|---|---|---|
| 26 | intended clean | 0 | — |
| 27 | intended clean | 0 | — |
| 28 | intended clean | 1 | **False confirm.** It demands punctuation stripping, non-ASCII handling and path separators — reasonable for a slugifier, but beyond the stated docstring contract. A scope-creep finding, not an incorrect one. |
| 29 | intended clean | 1 | **True positive.** `chunk(items, -1)` returns `[]` because `range(0, n, -1)` is empty. My seed was not clean; I wrote a defect. |
| 30 | intended clean | 1 | **True positive.** `mean(generator)` raises `TypeError` — the emptiness check consumes the iterator. My seed was not clean. |

- **Measured:** 1 false confirm in 3 genuinely clean pull requests (1/3). All five reviews returned
  `complete=True`, so none of these is an incomplete-review artefact.
- **Two of my own "clean" seeds contained real bugs.** That is worth recording rather than quietly
  re-seeding: it is direct evidence that small, plausible, clean-looking code often is not, which is the
  case for the product. It also means the intended-vs-adjudicated distinction is essential — taken at
  face value the seeds would have read as 3 false confirms in 5.
- **Not measured:** a rate with statistical meaning. Three clean inputs is a smoke test; a precision
  figure needs tens of pull requests across repositories and languages with independently known
  defects. `1/3` is one observation, not a rate, and must not be quoted as one.

## 7. Publication itself

| Requirement | Status |
|---|---|
| The repository is private | **Met** |
| Nothing is published to a package index | **Met** — not attempted |
| No repo settings, visibility or tags changed | **Met** |
| The plugin catalogue entry exists | **Not done** — deliberately out of scope |
| A licence file is present | **Check before publishing** |
| The README install steps have been run by someone other than the author | **Not done** |
| The plugin is installed in a live Hermes | **Not done** — and must stay that way until approved |

## 8. What must be true before this is called ready

1. The false-confirm rate measured across enough pull requests to mean something.
2. D13 decided (approve, comment-only, or defer) — it is the only remaining item that changes the
   threat model.
3. The surfaces redesigned (stop 5c) and visually signed off.
4. A second person runs the install steps from the README and gets a working review.
5. A decision on the six open design questions in `design-notes.md`.

Items 1, 3 and 4 are not engineering tasks; they need other people or other decisions. That is the
honest position: the code is close, the evidence is thin, and the remaining work is mostly not mine.
