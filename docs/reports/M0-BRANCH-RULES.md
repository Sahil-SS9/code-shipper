# M0 branch-rules follow-up — 24/11/2026

Scope authorised by owner: make the throwaway sandbox public and test GitHub branch rules.
No live application or Hermes settings changed.

Repo: https://github.com/Sahil-SS9/code-shipper-sandbox (verified PUBLIC).
Before publication: all 28 reachable git objects checked for the handed-over token, zero hits.
History contains ordinary commit-author metadata, including the owner's GitHub merge email.

Applied main-branch protection (verified from GitHub response):
- one approving review required;
- dismiss stale reviews;
- required `pytest` status, strict/up-to-date;
- enforce restrictions on administrators too;
- force-pushes and deletions disabled.

Executed `spikes/m0/c2_branch_rules.py` (raw receipts: `spikes/m0/out/c2-results.json`, ignored).

| Probe | Result |
|---|---|
| REST merge of PR 3 with green CI but no human approval | 405, at least one approving review required |
| GraphQL merge of same PR | null mutation + same approval-required error (HTTP 200 is not success) |
| Enable auto-merge | refused, auto-merge disabled for repository; NOT evidence of a branch-rule denial |
| Direct fast-forward push to main | rejected GH006, PR and required check expected |
| Same credential pushes a new working branch | succeeds, positive control |

Verdict: branch rules enforce the missing-human-approval gate and protect direct main pushes.
They do NOT prove that the fixer cannot merge AFTER a human approves and all checks pass.
GitHub may then allow a Contents-write identity to merge. No second identity's approval was
available in this test, so that case is NOT TESTED. Do not claim unconditional human-only
merge authority from these results. The broker must expose no merge operation and the agent
must not hold/reach the broker credential. The sandbox prerequisite remains blocked by
AppArmor on this host; no system configuration changed to bypass it.

Also not tested: whether this handed-over token can edit protections. It had broader access
than requested in the first run, so it cannot demonstrate a least-privilege production setup.
Replace/revoke it when testing is over. No production branch rules were touched.

Next: review/fix the spec's unconditional "token cannot merge" claims, then resolve sandbox
setup separately with owner approval before running autonomous fixes with a security claim.
