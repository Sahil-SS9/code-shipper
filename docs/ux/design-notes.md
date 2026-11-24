# Code-Shipper wireframes — design notes

Companion to `gallery.html` (CLI transcripts, TUI board, Desktop page, policy editor, audit trail).
Proposal for owner sign-off; nothing here is implemented. Fictional SHAs, receipts and timestamps.

## Layout logic

**One projection, three renderers.** All three surfaces draw the same revisioned state (PR, head,
reviewed-head, verdict, checks, policy, budgets, receipts) and differ only in presentation. This is
what makes cross-surface parity testable: same fixture in, same allowed actions out.

- **CLI** — receipt-first. Every command leads with the result or the blocker, then PR/head, next
  action, run ownership and the stop command. Deliberately flat: no pseudo-TUI in the transcript
  layer, so it pipes, greps and scripts cleanly. `--json` carries the same content as an envelope;
  unknown cost is `null`, never `$0`.
- **TUI** (`/ship-ui`, distinct from `/ship`) — two-region board: attention-sorted inbox left, live
  log + tabs right, persistent context line on top (connection, profile, runtime owner, sync age),
  keymap footer at the bottom. The footer always shows the keys that work *right now*. At 80 cols
  the regions collapse to list/detail; below the floor it degrades to `/ship` text rather than
  clipping actions.
- **Desktop** — three regions at wide width: inbox (270px) / working tabs / context rail (290px).
  The rail is deliberately not a second dashboard: it holds run ownership, budgets, the authority
  boundary and the safe actions. The inbox sorts *needs-you-first* and every row carries its reason
  in words — no invented "PR health score".

## Colour and state semantics

Dark operator console: near-black panels (`#0a0d0f`–`#13181b`), hairline borders, one amber accent
(`#e0a458`) reserved for attention and primary actions. Muted green `#7fbf7a` for pass/done, red
`#d96a5f` for fail/refused/Important, desaturated blue `#7ba7bc` for in-flight. No gradients, no
purple, no glow.

Hard rules:

- **Colour is never the only channel.** Every state chip is text + colour (`NEEDS-HUMAN`,
  `REVIEWING`, `READY`); diffs keep +/−.
- **No compressed "safe" badge.** Review verdict, CI, policy and approval are four independent
  signals and are shown as four signals. A green CI row never implies policy satisfaction, and a
  policy pass never implies a person reviewed anything.
- **Three verdicts only**: `APPROVE` (nothing posted), `APPROVE-WITH-ISSUES` (nit/pre-existing),
  `NEEDS-FIX` (Important open) — always pinned to the exact head SHA they were computed at.
- **The authority boundary is shown as a state, not a claim.** "advisory — unverified (M0 open)"
  appears wherever authority is discussed, until the broker/sandbox proof lands. Amber, never green.
- **Refusals are first-class output.** `ship approve` failing with `refused: protected-path` gets
  the same typographic weight as a success, and the same row in the audit trail.

## Key interaction decisions

1. **Merge is absent by design.** No `ship merge`, no merge key in the TUI, and on Desktop a
   *disabled* "Merge — not available" control with its reason beside it. The terminal state is
   "ready for human merge" with one action: Open PR on GitHub. Absence alone is ambiguous (a user
   may hunt for the button); a visibly disabled control with the reason teaches the boundary.
2. **Stop ≠ cancel, and Escape is neither.** `s` stops watching (durable fence; the current round
   settles and is labelled as such), `x` cancels the current run (revokes unissued writes, settles
   in-flight receipts). Escape only closes the view. This mirrors Claude Ship's documented
   stop-is-not-cancel trap and fixes it with two labelled actions instead of one ambiguous square.
3. **Single-key confirms with a safe default.** The approval prompt is keyboard-fast (`r` request,
   `esc` back) but Enter/landing focus sits on Back. Disabled options stay visible with their
   reason ("agent approve — disabled: authority flag off"), not hidden.
4. **Policy is explained per file, expanded by default.** Claude collapses "How the rules led to
   this verdict"; we show the matches/applies table per changed file, strictest-wins line, and
   counted approvals at head — because "which rule governs this file" is the question a maintainer
   actually has. Proposed policy edits are a separate, clearly badged pane that is validated live
   and never self-applied.
5. **Budgets are meters with units, plus honesty about unknowns.** Rounds 1/3, pushes 1/10, wall
   clock mm:ss — and spend shown as "unknown — harness doesn't meter" in amber, never a green gauge
   over an unmeasured adapter.
6. **Every write names its head and its undo.** Pushes print the expected-head check, the new SHA,
   the receipt ID, and `git revert <sha>` — with the steward warning (stop the watch first, or it
   may re-make the fix), which Claude documents but buries.

## Three ways this beats Claude Ship's UI

1. **An attention-first multi-PR inbox.** Claude's `/ship` surface is a single-PR session stream
   ("keep this session open, computer awake, branch checked out"). Every surface here opens on a
   durable inbox sorted by what needs a human, with the reason in words, and the watch runs on a
   scheduler that survives the terminal. Time-to-"what needs me" drops from reading a transcript to
   reading one row.
2. **Separable trust signals.** Claude's approvals screenshot compresses policy verdict, approving
   review and CI into one "Passing" cluster; its review image treats "thread resolved" as settled.
   These wireframes keep review verdict / CI / policy / approval as four labelled signals, pin each
   to the exact head, and keep "resolved" distinct from "verified fixed" (findings carry evidence
   kind and a re-check at current head).
3. **Honest authority and refusal surfaces.** Claude blocks merge/approve by command-text matching
   and its own docs admit a differently-worded command passes. Rather than a safety claim, our UI
   shows the boundary as *advisory until proven*, makes refusals (`protected-path`, authority flag
   off) first-class output, and logs them in the same audit trail as writes — including uncertain
   writes that settle by read-back instead of silent retry.

## Open questions for Sahil

1. **Desktop merge affordance**: is a visibly *disabled* "Merge — not available" button the right
   teaching device, or does any merge-shaped control on the page cross the line for you?
   Alternative: remove it entirely and rely on the "Open PR on GitHub" action.
2. **Agent-approval presentation when the D1 flag is on**: the mock shows the disabled state. When
   enabled, should `a` (grant agent approval) require a two-step confirm (press `a`, then confirm
   line naming identity + head), or is the single-key prompt with the policy summary sufficient?
3. **TUI transport fallback**: if the authenticated widget-RPC seam stays unproven, `/ship-ui`
   degrades to `/ship` text + pager. Is that an acceptable v1 story, or is interactive TUI a
   release gate? (Recommendation in ux-review.md: degrade explicitly, don't fake parity.)
4. **Density floor for the Desktop page**: the mock targets ~14px body with a 270px inbox. Do you
   want a compact density toggle, or is one density fine for v1?
5. **Audit retention and scope**: the audit view is per-repo here. Do you want a global
   cross-repo ledger view in v1, and how long should receipts persist locally?
6. **Verdict naming**: `APPROVE-WITH-ISSUES` is precise but long for inbox chips (`READY` is used
   there as shorthand for "policy satisfied, waiting on human"). Keep the shorthand, or carry the
   full verdict into the inbox at the cost of wider rows?

## Verification of this deliverable

- `gallery.html` is self-contained (inline CSS, no JS, no external assets).
- All TUI box-drawing blocks machine-checked for equal line display widths
  (`_check_widths.py`, kept in this directory): board 113 cols, dialog 88, compact 74 — all aligned.
- Rendered headless (Firefox, 1400px) and visually inspected: transcripts legible, boxes aligned,
  three-pane desktop mock, policy editor and audit table render without breakage.
- Not done: no keyboard, screen-reader, contrast-measurement or host-integration testing — those
  follow visual approval.
