# M0 report — falsification spike

Verdict: READY FOR REVIEW — the core safety claim is FALSE as specified; spec changes required.
Date: 24/11/2026. Test repo: https://github.com/Sahil-SS9/code-shipper-sandbox (private, owner account on GitHub Free).
Scripts: `spikes/m0/c1_fixer_token.py`, `c4_agent_reach.py`, `c5_fix_loop.py`. Raw output is in
`spikes/m0/out/` (gitignored); the results are copied below.

## Gate items

- T-SPIKE-01 fixer token pushes to a PR branch: VERIFIED (allowed, as wanted).
- T-SPIKE-02 fixer token approves: VERIFIED refused, but only with 422 "Can not approve your own
  pull request", because the token belongs to the PR author's own account. Not a permission refusal.
- T-SPIKE-03 fixer token cannot merge: REFUTED. `PUT /pulls/2/merge` returned 200 "Pull Request
  successfully merged". With no branch rules the same token also pushed straight to `main`,
  pushed a tag and deleted a branch. (GraphQL merge then failed only because PR 2 was already
  merged; auto-merge is not enabled on the repo.)
- T-SPIKE-04 approver token: NOT DONE (no second account; not needed until agent approval).
- T-SPIKE-05 agent process cannot reach credentials: REFUTED unsandboxed. With a scrubbed env and
  a private HOME the probe could still read `~/.config/gh/hosts.yml`, the fixer token file,
  `~/.hermes/.env` and `~/.ssh/id_ed25519`, and `gh auth token` with `GH_CONFIG_DIR` pointed at the
  real config returned the owner's 40-character token. The sandboxed run could not happen:
  `bwrap: setting up uid map: Permission denied`.
- One fix loop to green: VERIFIED. PR 3: CI failed → Hermes one-shot in a worktree (scrubbed
  env, 40 s) fixed `calc/__init__.py` → scope check passed → local tests 3 passed → exact-head
  check → broker pushed with the fixer token (hooks off, no owner credential helper reachable) →
  change/undo comment → CI success.

## What the results mean

1. A fine-grained token with Contents: write can merge PRs and push to any branch, including
   `main`, unless GitHub branch rules stop it. Fine-grained tokens cannot be limited to one
   branch. So "the fixer's token cannot merge" is only true when branch rules are on.
2. Branch protection and rulesets on private repos need GitHub Pro/Team. On this account both
   API calls return 403 "Upgrade to GitHub Pro or make this repository public". Public repos get
   them free.
3. Without an OS sandbox, a same-user agent can read every credential on disk, including the
   owner's admin token. The broker allowlist then only stops an agent that follows instructions.
4. Bubblewrap is blocked on this machine: `kernel.apparmor_restrict_unprivileged_userns = 1`
   (Ubuntu default) and no AppArmor profile for `/usr/bin/bwrap`. Docker is installed, but the
   `kensei` user is not in the `docker` group. Either fix is a system change for the owner.
5. The review → fix → push → green loop itself works and is fast.

## Deviations from spec

- The comment on PR 3 was posted with the owner's gh login, not through a broker role.
- The handed-over token is broader than requested: it reads another private repo
  (`Sahil-SS9/Severian` returned 200), can post PR reviews (Pull requests is not read-only), and
  expires in 30 days rather than 7. It was used only against the test repo. The owner should
  delete it after testing.

## Spec changes forced (to apply after owner review)

- Tech spec §6: the authority boundary has two required layers for any "cannot merge" claim:
  (a) GitHub branch rules (require PR, require approval, require `code-shipper/approvals`, no
  bypass for the fixer), and (b) an OS sandbox that hides credential files from the agent.
  `doctor` checks both. Without them Code-Shipper says plainly that the boundary is advisory.
- `allow_agent_approval` additionally requires (a) verified by API at enable time and (b) active.
- Linux sandbox setup documented: an AppArmor profile for bwrap, or rootless podman.
- On GitHub Free private repos, the README states that GitHub cannot enforce the limits.

## Open risks

- No test yet that branch rules actually stop the fixer token (needs a public test repo or Pro).
- No sandbox run yet (needs a system change).

## Owner questions

1. Test branch rules: make the throwaway test repo public for the test, or skip?
2. Allow a one-time system change so the sandbox works (AppArmor profile for bubblewrap)?
3. Delete the over-broad token after testing, and make a narrow one if we continue?

## Plain-English summary

The fix loop works: a broken PR went to green with no human help in about two minutes.
But the key safety promise failed. The fixer's key could merge and push straight to main,
because private repos on a free GitHub plan have no branch rules. And an unsandboxed agent
can read your own GitHub login from disk. Both are fixable: branch rules (public repos or
GitHub Pro) and a sandbox (a one-time system change). Until then, Code-Shipper must call
its limits advisory.
