# P2-C — The honesty pack

Status: complete, awaiting review. Nothing posted to any repository by these tests.
Commits: `64b5955` (the pack), `6b0532e` (the two required changes)

Eight items: C1, C2, C4, C6, C7, C8, C16, C20. C4 is the prerequisite for Phase 3 incremental review,
so it is first and it carries a measurement that changed the design.

## C4 — the stable fingerprint, and a deviation that a measurement forced

D11 specifies: path + normalised title + hash of the quoted evidence.

**That does not work, and I can show why.** Two real reviews of PR #18 at the same head produced:

| Run | Title | Evidence |
|---|---|---|
| 1 | "divide() guard **changed** from b == 0 to b <= 0, so **every negative divisor** was rejected" | identical |
| 2 | "divide() guard **widened** from b == 0 to b <= 0, so **negative divisors** now raise" | identical |

The titles are the model's own wording and it does not reproduce them. Including the title splits one
finding into two, which is precisely the duplicate D11 exists to prevent.

So identity is **path + quoted evidence**, with the title used only when a finding quotes nothing.

This keeps D11's stated goal and its method:
- D11's acceptance test — "shifting the code by N lines does not re-post the same finding" — holds **by
  construction**, because the line number is not an input at all.
- D11 says explicitly *not* the line number, and this is stricter than what it literally says.
- `tests/test_p2c.py::test_two_real_runs_over_identical_code_agree` pins the measurement, and asserts
  first that the two titles really did differ — otherwise the test would pass for the wrong reason.

Evidence is normalised so a re-indent, a formatter pass, or quoting with or without diff markers does
not change identity. Diff file headers (`+++`/`---`) are dropped entirely: the path is already in the
fingerprint, and including the header made identity depend on whether the model happened to quote it.

## C7, C8 — review state is always stated

A silent review is indistinguishable from a broken one, so absence is always stated in words:

- **No findings** → "No findings at standard depth over combined. This is a complete review."
- **Incomplete** → "**INCOMPLETE** — this review did not finish, so the absence of findings is not a
  clean result", followed by the reason.
- **Incremental** → states the commit it is incremental from.
- Om omitted pre-existing, omitted nits and dropped unverified candidates are each **counted**, so
  "nothing here" can never mean "we did not look".

## C16 — machine-readable verdict

A fenced JSON block carries state, level, profile, lenses, counts and fingerprints. A script reads the
verdict without regexing prose. A test asserts the block contains no `approved`, no `merge`, and no
`request_changes` — not even as negations, because a reader skimming for "approved" should find
nothing.

## C20 — cache on (repo, head_sha)

Keyed on the **head SHA**, never the PR number. A PR whose head moved is genuinely different content,
and serving the previous review would be stale in the worst way: confident, plausible, and about code
that is no longer there. A corrupt entry is a miss, not a crash — a cache that can fail a review is
not worth having.

## C1 — path filters

Default ignores for lockfiles, minified bundles, source maps, generated output, vendored code and
binaries. Extra ignores are additive; turning the defaults off is an explicit `include_ignored`.

Ignored files are **counted and named in the review body**: "3 file(s) not reviewed (lockfiles,
generated output and vendored code): package-lock.json, …". Without that line, "no findings" could
quietly mean "nothing was read".

## C6 — rationale, collapsed

Each finding can carry why the lens raised it and what made verification keep it, inside a
`<details>` block, so it is available without pushing the claim out of view. A comment without a
rationale is just the finding.

## C2 — wrap-up reserve

Covered by the existing budget behaviour rather than new machinery: an exhausted budget raises
`BudgetExceeded`, and the caller produces an `INCOMPLETE` report rather than a silent stop. The test
asserts the incomplete report is rendered and the budget records what it used.

## The two required changes

**1. `head_branch` is required.** It defaulted to `None`, and `None` meant "any branch is fine". The
one real caller did not pass it, so T-ADV-06 was documentation rather than a control. It is now a
required keyword with no default; `cp4_run.py` passes the PR head. Four tests:

| Test | Asserts |
|---|---|
| omitting it | `TypeError` — the omission cannot compile, let alone push |
| empty string | refused at `branch-scope`, remote unchanged |
| a branch that is not the head | refused at `branch-scope`, remote **verified** unchanged |
| signature inspection | the parameter has **no default** |

**2. fixloop's duplicate scope rules are deleted.** `ALLOWED_PATTERNS`, `FORBIDDEN_PATTERNS`,
`_allowed`, `_posix` and `_tests_only_tightened` are gone. `check_scope` and `test_weakening` take a
`ScopePolicy` and route everything through `steward/policy.py`. The default policy allows nothing, so a
caller that forgets one gets a refusal rather than a permissive pass — the same fail-closed shape as
the broker's role table.

The structural test the review asked for: no attribute of those five names exists on the module, and
fixloop's own source contains no `calc/`, no pattern names, no `conftest.py`, no `@pytest` and no bare
`assert`. That last set is what makes it a real check on the *logic*, not just the names.

One thing this caught: the old hard-coded rule restricted calc to `*.py`, so `calc/data.json` was out
of scope. My first policy patterns (`calc/**`) allowed it. Tightened the test policies to
`calc/**/*.py` to preserve the original restriction rather than silently widening scope during a
refactor.

## Not done

- **C3 incremental review** itself is Phase 3. C4, its prerequisite, is here.
- **C9–C15** are Phase 3 and 4b.
- **Nothing was posted.** All tests are local; the fingerprint measurement uses the receipts already
  in `docs/reports/evidence/P2/`.

## Reproduce

```
cd /home/kensei/repos/code-shipper
./scripts/run_tests.sh                                  # 521 tests
python3 -m unittest tests.test_p2c -v                   # 43 tests, the honesty pack
python3 -m unittest tests.test_fixloop_scope -v          # 23 tests, incl. the structural check
```

## Test output (real)

```
$ ./scripts/run_tests.sh
== unit tests
...
Ran 521 tests in 108.705s

OK
```

Exit status 0, recorded by the runtime.
