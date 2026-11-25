# P3-B part 2 — Contained fix run on sandbox PR #23

Status: complete, awaiting review. One fix pushed, CI green, nothing merged or approved.
Commit: `80aac84` (classify/workspace/fix) + the deletion regression fix

## What ran

| Step | Result |
|---|---|
| PR | **#23**, branch `p3b-string-format` |
| Head before | `71b4ceba90eb` |
| CI before | run **45524213389** — **failure** |
| Container | rootless, `--network none`, uid 65534, 27/27 canaries pass |
| Canary failures | **none** |
| Model calls | 5 completions, 0 refused |
| Scope verdict | **in scope** — `changed: ['calc/__init__.py']`, `removed: []` |
| Push | `71b4cea → 01cc438`, fast-forward at the exact expected head |
| CI after | run **45526563303** — **success** (`pytest` pass) |
| Merged / approved | **neither. There is no merge operation in the product.** |

## The fix, read back from GitHub

The pushed commit restored `divide()` to returning a number:

```
-def divide(a, b):
-    if b == 0:
-        raise ZeroDivisionError("b must not be zero")
-    result = a / b
-    return str(result) + " items"
+def divide(a, b):
+    if b == 0:
+        raise ZeroDivisionError("b must not be zero")
+    return a / b
```

`tests/test_calc.py` was **not touched** — the fix changed the cause, not the test. Scope confirms
`removed: []`, and the AST check found no weakening.

Because the fixed file now equals `main`, PR #23 currently shows **0 changed files**. That is the
honest outcome of the fix, not a vanished push: `gh api .../contents/calc/__init__.py?ref=p3b-string-format`
returns the corrected numeric version, and `gh pr view` reports `headRefOid 01cc438`, `state OPEN`.

## Two real bugs found, both by running it rather than reasoning about it

### 1. A fix arrived as a deletion of the whole repository

The first push (`d8f6d2f`) produced a diff deleting `.github/workflows/tests.yml`, `README.md`,
`calc/__init__.py`, `tests/test_calc.py` and two data files.

**Cause.** The workspace was built with `git init` followed by `git reset --soft FETCH_HEAD`. `--soft`
does not touch the index, so the index still described the *empty* fresh repository. `commit -a` then
recorded every file as deleted, and the fast-forward push delivered it faithfully.

**How it was caught.** Reading the diff back from GitHub, as the checkpoint requires. The push
reported `ok: true` with a clean fast-forward; every host-side signal said success.

**Fix.** `--mixed` rebuilds the index from the fetched tree, and `_materialise` now asserts the working
tree still contains every top-level entry from the source, raising rather than continuing. Regression
test `test_a_fix_does_not_commit_every_file_as_deleted` builds a real bare remote, pushes a one-line
fix, and asserts the commit touches one file, contains no `deleted file`, and that `README.md` and
`tests/test_calc.py` survive in the tree.

**Recovery.** The branch was reset to `71b4ceba90eb` via the API; the diff was verified back to the
original single-file change before re-running.

### 2. Classification refused the work for the wrong reason

The first run classified the failing check as "nothing to do", reason: *"nothing in its output points
at a file this PR changed"*. Literally true — the PR changed `calc/__init__.py` and the assertion
failed in `tests/test_calc.py` — but the conclusion was wrong, and it was the ordinary case for any
source-only PR.

**Fix.** A log-named test file now counts as related when it **imports a module the PR changed**,
resolved by parsing the real file with `ast`. Without that import link it still refuses, so an
unrelated failing test does not become work. The rule needed the evidence, not just the path.

## What this checkpoint does not prove

- **One provider.** Every call went to local Turbofit on loopback. Real hosted-provider auth through
  the broker is still unmeasured.
- **One PR, one failure mode.** A conflict, a review comment and an Important finding are classified
  and unit-tested but have not been driven end-to-end against GitHub.
- **No uncertain-write in anger.** Reconciliation is tested but this run had no 5xx.
- **Cancellation** is P3-C.
- **The push used a real token**, which is why the deletion bug reached GitHub. The broker's role check
  and scope check both passed a change that was destructive in a way neither looks for: they judge
  *where* a change writes, not whether the commit preserves files it never mentioned. That gap is now
  closed by the tree assertion, and worth noting as a class of bug this design did not anticipate.

## Reproduce

```
cd /home/kensei/repos/code-shipper
python3 -B spikes/b1/p3b_run.py --pr 23 --json          # full run, pushes
python3 -B spikes/b1/p3b_run.py --pr 23 --no-push       # stop after the scope check
python3 -m unittest tests.test_steward_fix -v           # 35 tests incl. the deletion regression
```

Receipt: `docs/reports/evidence/P3B/receipt.json`.

## Test output (real)

```
$ ./scripts/run_tests.sh
...
Ran 572 tests in 112.889s

OK
```

## Checkpoint CI

Run **45530288577** — **6/6 success** across ubuntu, macOS and Windows on py3.11 and py3.12.

It took four attempts, and the failures were worth having:

| Run | Result | What it found |
|---|---|---|
| 45527032812 | 0/6 | `require_private_dir` checked mode bits Windows does not have (`mkdtemp` reports 0o777 there) |
| 45528092982 | 0/6 | The credential-path guard matched only `/`-separated strings, so a Windows `C:\Users\x\.hermes` was **mountable**. Cleanup failed on Windows. A socket test spent a shared capability. |
| 45529081851 | 2/6 | Windows green; POSIX still failed on a test that asserted a rule which does not exist |
| 45530288577 | **6/6** | — |

The second one is the finding that matters: the guard that stops a credential directory being mounted
into the container silently did not work on Windows. It was written, reviewed and passing on Linux.

