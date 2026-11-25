# Wiring stop — the surfaces read real state

Status: **stopping for review.** Commits `99bceb6`, `7b7aaf2`, `96b1687`, `59fe585`, `ff88bf8`. Tests
**1189, OK**.

## Done / Not done against the instruction

### The fix: one loader, fixtures only behind `--demo` — **Done**

`code_shipper/surfaces/loader.py` is the single entry point:

- `load_snapshot(repo, pr)` reads `ReviewStore` (head, open findings, pause reason), `Ledger` (tracked
  findings with actor and timestamp) and `ReviewCache` (richer finding detail).
- `build_projection(snapshot)` hands them to the existing `build_from_review`.
- `load_projection(repo, pr)` is what every surface calls.
- `discover()` and `rows_for_state_dir()` answer "what has been reviewed" from the state directory.

`status`, `ui`, `ship-ui`, `ship-page` and the plugin's new `/ship ... board` all use it. `--demo` is the
only route to fixtures, and when it is used every surface prints:

```
-- demo data: sample values, not a real review --
```

or the equivalent banner in the HTML page.

| Item | Status |
|---|---|
| 1. `status` ignores `--repo`, prints fixtures | **Done** — reads stored state, honours `--repo`, `--state-dir` and `--demo` |
| 2. `ui` / `ship-ui` / `ship-page` only draw fixtures | **Done** — all three go through `_projection_for`, which uses the loader |
| 3. `review` prints "not implemented yet" while doing nothing | **Done** — it reviews and stores, then says plainly that it did not post and that `watch`/`poll` do |
| 4. `fix` without `--dry-run` says use `watch`; README should say so | **Done** — README documents it |
| 5. Plugin has no Desktop surface | **Decision made, documented** — no separate Hermes Desktop panel; `/ship ... board` draws the stored review, desktop surface stays the HTML page from `ship-page`. README says this explicitly |
| 6. Ctrl+K hint with no palette | **Done** — the palette is drawn inline from `palette.group_items()` |
| Tests: each surface on real stored state | **Done** — 33 tests in `tests/test_surfaces_real_state.py` |
| Test that fails if a surface reads fixtures without `--demo` | **Done** — source-level AST sweep, proven by negative control |

### On item 5, the decision and why

I chose **no separate Desktop panel**. A panel inside the Hermes desktop would be a fourth place holding
presentation logic, which is precisely what caused this stop: the surfaces drifted from the service because
they each knew how to draw. The desktop surface stays the self-contained HTML page `ship-page` writes from
real state, and `/ship ... board` covers the in-agent case. The README says so rather than implying a
surface that does not exist.

### Proven against the old behaviour, not just asserted

Both guards were run against a deliberately broken tree:

| Guard | Old behaviour | With the fix |
|---|---|---|
| `test_no_surface_imports_fixtures` | FAILS (1) when `tui` imports fixtures | passes |
| `test_the_cli_only_touches_fixtures_inside_the_demo_path` | found 2 real offenders when first written | passes |

The second one earned its place immediately: it caught `cmd_ship_ui`, which still had its own
`fixtures.ALL[args.state]()` body long after `ui` was rewired. Without the sweep, `ship-ui` would have kept
showing sample data while `ui` showed real reviews.

### Real end-to-end, not just unit tests

State written the way the review service writes it, then drawn by the CLI:

```
$ code-shipper status --state-dir /tmp/realstate2
all repositories  · watching 1 · checked every 2 min

  ● #25          Needs fixes          Fix 1 important finding

$ code-shipper ui --state-dir /tmp/realstate2 --width 100
 NEEDS YOU  1          │ #25     Needs fixes
▸●                     │  →  · head 9c2eca0a
   Fix 1 important...  │▌NEXT  You decide on 1 important finding
```

Three defects came out of doing this for real, none visible from the unit tests:

1. **Infinite recursion.** `load_projection` → `rows_for_state_dir` → `load_projection`. A `RecursionError`
   with a 158,000-character traceback. Fixed with an explicit `with_inbox=False` at that call site.
2. **`#25 #25`.** The projection invented a title by repeating the pull request number. Now empty when
   nothing is stored.
3. **`status` said "No findings at this head" for a review that had a finding.** It was using the Next
   headline as the reason column instead of the short reason.

## Not done

**CI has not run.** Run `45705276383` on `ff88bf8`: **0/6, and no job started.**

```
The job was not started because recent account payments have failed or your spending limit needs to be
increased. Please check the 'Billing & plans' section in your settings
```

Every job failed in 2–3 seconds without executing a step. Billing is still not fixed on the account. So
**none of the five commits in this stop has matrix evidence** — only the local suite against the exact
committed bytes, 1189 tests, OK.

That matters more than usual here. Two of the six defects in this round were only visible by running the
thing, and the py3.11 bug earlier was found by the matrix and missed locally. I am not claiming this round
is matrix-clean.

## Test output (real)

```
$ ./scripts/run_tests.sh
Ran 1199 tests in 513.408s

OK
```

## Two more defects found after the first report

Both found by running the committed tree in a clean `git worktree` instead of from the working directory —
the check I should have been doing throughout, and now do.

1. **`ship-ui` crashed on every invocation.** It delegates to `cmd_ui`, which reads `args.json`; the
   `ship-ui` subparser has no such flag, so it raised `AttributeError`. Tests passed because they called
   `tui.render` directly and never went through `main()`. Fixed, and covered by ten tests that invoke the
   real entry point.
2. **`cli_view.py` was missing from its commit.** The demo banner helper was left out of `7b7aaf2` while
   the CLI imported it, so `status --demo` would have failed in the committed tree.

Verified afterwards in a detached worktree of `a61ed47`:

```
status --demo                              -> exit 0
status --state-dir <real>                  -> exit 0
ui --state-dir <real> --width 100          -> exit 1  (findings)
ship-ui --state-dir <real>                 -> exit 1  (findings)
ship-ui findings --demo                    -> exit 1
ship-page --state-dir <real> --out ...     -> exit 0, finding in page, no demo banner, palette drawn
```

## Commit hygiene

Seven commits: `99bceb6` (loader + tests), `7b7aaf2` (CLI wiring), `96b1687` (palette + demo banner),
`59fe585` (plugin `board`), `ff88bf8` (README), `a9bceab` (the missing `cli_view.py`), `a61ed47`
(end-to-end tests + the `ship-ui` fix).

Two hygiene failures in this stop, both named rather than amended:

- `cli_view.py` missed `7b7aaf2`. Third stop in a row with a file left out.
- `a9bceab` was itself committed with broken indentation in `cli.py` — the edit landed outside the
  function. The suite caught it immediately, which is why the fix went into `a61ed47` with the tests.

The pattern: `git add <named files>` misses what I did not think to name. Committing the tree and reading
`git status` before pushing is the habit that would have prevented all three.

## Still unproven

- CI on any of the five commits — billing.
- A real hosted-provider authentication run; macOS/Windows containment; broker concurrency under load; PSI
  isolation; a false-confirm rate at any meaningful sample size.
- The `board` subcommand has not been exercised inside a live Hermes session — only its argv construction
  is tested.

The sandbox PRs (#25–#30) remain open and unmerged. Nothing is published. `~/.hermes` untouched.
