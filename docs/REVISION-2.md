# Revision 2 — research, authority and cross-surface corrections

2026-11-24: consolidated into the canonical docs; body retained as audit trail.

Status: design/specification revision, NOT build approval or proof of confinement.
Owner authorises a deeper research pass, fixing isolation and revising the spec, with CLI,
interactive TUI and Desktop required. No additional live deployment or global security weakening.

Precedence: binding owner decisions in decisions.md, then this correction document, then the
original PRD/architecture/tech-spec/build-plan. Any affected legacy wording is superseded by
this document. A builder must reconcile it into the original documents before implementing.

## 1. Facts and claim corrections

- M0 proved one real failing-PR -> Hermes fix -> local tests -> broker push -> GitHub CI success.
  This is a spike, not a completed Code-Shipper product.
- A Contents-write GitHub token CAN merge on an unprotected repository. Branch protection
  refused REST/GraphQL merges while approval was missing and refused direct main pushes.
  It does NOT prove that a fixer token cannot merge after approvals and checks pass.
- Human-only merging is a product constraint: Code-Shipper exposes no merge/auto-merge action,
  no user-facing bypass, and no agent-accessible merge capability. It is not a claim that every
  credential used by the trusted host is incapable of merging through other software.
- The model and arbitrary repository code must not reach GitHub credentials. The authority
  broker mediates all writes with an operation allowlist and current authority revalidation.
  Reviewer findings are COMMENT-only; the policy approver is distinct from fixer and author.
- The initial 'no docker group' diagnosis missed an existing rootless Docker daemon. Parent
  verified `docker context show` = rootless and security options include rootless and seccomp.
  No sudo, docker-group change or global sysctl change is needed to INVESTIGATE this route.
  Container confinement was UNVERIFIED at revision-2 authoring. Owner subsequently explicitly
  reauthorised the probe: `reports/M0-ROOTLESS.md` records twenty passing controls on a cached
  Linux rootless container. Basic filesystem/process/network separation now has bounded evidence,
  but contained Hermes + host inference broker integration remains UNVERIFIED. Rootless Docker
  availability alone is not evidence of the full product boundary.
- Rootless Docker root commonly maps to the daemon owner's host UID; never rely on 'all host
  credentials are unmapped' as the boundary. Mount visibility, process isolation, socket access,
  network policy and credential separation must each be tested.
- diaktoros remains material prior art: host broker, identity separation, bounded rounds,
  `human_paths`, shadow/adjudicator roles. Its configuration documents a reviewer approval gate
  on matching paths, as well as an adjudicator gate. Experimental native macOS exists, not
  production-supported. Negative claims in later research conflicting with this are unresolved
  until compared at one pinned revision. Do not silently replace verified facts.
- Neither policy-based approvals nor a review/fix loop is unique. Research identified additional
  approval competition including GitHub Copilot. Re-open primary docs before public comparisons.
- Claude Ship's regulated-plan exclusions are not evidence Code-Shipper is compliant with HIPAA,
  FERPA, ZDR or CMEK requirements. No such claim is authorised.

## 2. Safety design (supersedes tech-spec sections 2.2, 5.2, 6 where inconsistent)

Required for ALL autonomous fix modes, including single identity:
1. A contained worker receives an exact-head source snapshot and only its writable workspace.
2. It receives no host HOME, gh/git/SSH stores, model auth stores, Docker socket, system bus,
   host process namespace or arbitrary host filesystem mount. No privileged/host-network mode.
3. Harness authentication stays in a trusted host-side inference adapter. Do not copy secret
   configuration into the worker, or mount it read-only and call it safe. Model calls use a
   per-run bounded capability scoped to the configured model/provider and budget, not a generic
   HTTP proxy. This transport is a Phase 0 prerequisite, not an assumed existing feature.
4. Default worker network is denied. If dependency fetch is needed, resolve on the host through
   a separately scoped fetch path or prepare a pinned image; do not hand arbitrary egress to
   repository code. The inference endpoint must not expose general GitHub/host access.
5. Broker validates repo identity, PR eligibility, base/head refs, expected head/base/policy,
   current authorisation, budgets and cancellation generation immediately before every write.
   Push uses an atomic expected-old-ref lease, confined to the PR branch and fast-forward only.
   A preflight GET then ordinary push is insufficient to exclude concurrent head changes.
6. Record residual race: Git ref update cannot atomically condition on GitHub PR open/base
   metadata. Recheck and reconcile after write, disclose the race; never claim it eliminated.
7. Do not automatically replay ambiguous writes. Ledger states planned/posting/confirmed/
   refused/uncertain and reconciliation must cover crash before and after sending.
8. Platform doctor reports VERIFIED, UNVERIFIED, or UNSUPPORTED per isolation/backend/adapter.
   Fail closed for unattended fixing where required containment is unverified. Reading/review
   remains possible without pretending fixing is safe. No native Windows/macOS claim from Linux.

Containment acceptance (both negative and benign controls): hide host files including fixer
key, gh config, SSH, model auth and known-secret canaries; deny Docker/system sockets and host
process reads; deny arbitrary network and GitHub access; permit source read/workspace writes,
configured tests and bounded inference. Stop must revoke new write capabilities. Capture image
ID/digest, complete mount/env/namespace/network policy, effective limits and evidence. Use
synthetic canaries rather than opening real secrets for a repeat probe. Do not install/change
AppArmor or disable userns restrictions as a shortcut.

## 3. Product contradictions resolved

- Quick review reduces lenses, NOT verification. All posted findings require a separate
  refutation/verification step. A failed/incomplete review is not 'no issues'.
- A first publish plan (commit/push/open PR) needs explicit confirmation, not five-second silence.
  An operator can separately authorise bounded unattended writes for a repo. No blanket `--yes`
  disables the broker or secret scan. Record approved files/head/branch and invalidate on change.
- Monetary budgets: unknown is unknown, never zero. Refuse a claimed dollar-capped run if the
  adapter cannot enforce/reserve that cap. Subscription adapters need measurable wall/turn/token
  bounds and an explicit explanation that these do not impose a provider billing cap.
- Stop-watch: no new rounds, with in-flight outcome shown. Cancel-run: revoke write capability,
  stop the worker, reconcile any in-flight remote operation. Neither deletes a PR, resets user
  files, claims a sent push was undone, or blindly resumes an uncertain write.
- The example policy in tech-spec §4 is INVALID PRODUCT GUIDANCE: strictest-wins means the
  catch-all human rule prevents the low-risk rule relaxing it. Replace with strict base rules
  plus ordered conditional overrides INSIDE a rule; across rules/paths strictest still wins.
  Never permit missing classifier output to relax a base rule. This grammar must be specified
  and tested before implementation; do not build the example as-is.
- Default-branch policy and trusted operator configuration cannot be weakened by a PR. Protected
  policy/review/CI files always require a person; no classifier can self-exempt protected paths.

## 4. Research scope: deliberate parity, not a thin imitation

Produce a source-cited feature matrix with classifications: REQUIRED V1, INTENTIONAL DIFFERENCE,
FOLLOW-UP WITH OWNER APPROVAL, or UNVERIFIED. Do not silently drop product behaviour.

Research found these material missing concepts to reconcile: nested per-directory policies and
inheritance, ordered overrides, reusable policy definitions, typed model classifiers with
capability and budget scopes, result expiry, finding-resolution authority, approval invalidation
and reuse across pushes, classifier failure/timeout behaviour, author/bot/seat eligibility,
review/check provenance, draft/fork/Enterprise restrictions, local versus hosted lifecycle,
stop/restart, billing limits and provider terms.

Required safety first: invalidate by new head/base/policy/review/check provenance; stale verdict
never authorises new approval. Start with NO approval reuse until its exact proof contract is
reviewed. Rich nested/classifier semantics need written grammar/limits and benign plus malicious
fixtures, not a claim of Claude-schema compatibility. Remaining parity choices go to the owner
in one concise decision packet after the matrix is validated.

The original direct-API adapter is review-only and does not deliver automated fixing in Actions.
That is an explicit current design gap, not fulfilment of owner D3/D5. Specify a contained
provider-neutral tool loop or mark that mode incomplete for owner decision. Never list it as
full Actions parity merely because a workflow YAML exists.

## 5. First-class CLI, TUI and Desktop (owner requirement)

One shared state, event schema and authenticated action service; presentations do not implement
independent policy. No merge button on any surface.

CLI: scriptable subcommands, stable JSON output and exit codes; readable concise receipts;
watch/list/status/inspect/review/fix/stop/cancel/retry and dry-run publication plan. Non-TTY input
must not hang on confirmation; fail with an actionable message.

TUI: genuine keyboard PR inbox and detail view, tabs for diff/findings/checks/policy/activity,
search/filter, focus/selection, shortcut help, selected-run controls and live stale/error states.
A slash command returning text is NOT delivery of an interactive TUI. Upstream user widgets
exist, but authenticated backend transport/packaging is still a feasibility gate.

Desktop: native Hermes Desktop plugin page plus contextual pane where supported. Inbox -> PR
workbench with diff/line-anchored evidence, CI failures, per-rule approval explanation, run timeline,
budgets and explicit safe actions. Use host routes/keybinds and scoped backend, not a second
Electron process. No fake backend calls or a screenshot declared integrated.

Common states: loading, empty, offline, stale head, denied permission, missing credential,
unverified containment, running, waiting on CI, needs-human, budget exhausted, cancelled,
uncertain write and recovery. Show policy verdict, actual review approval, CI and merge eligibility
separately. A green check is never a blanket 'safe' badge.

Accessibility/quality: keyboard-only primary journeys, visible focus, non-colour status labels,
light/dark contrast, long paths/large diffs, resize/narrow panels, reduced motion, readable density,
consistent selected/focus state. Compare time-to-find-blocker and actions-to-open-evidence against
reference workflow; do not promise 'better' based on appearance alone.

Before UI code: owner approves wireframes and visual direction. Then browser/native TUI/host
runtime evidence for each real action, including cancellation, reconnect, stale-head and denied
operation controls. Independent views must converge after an action or show explicit staleness.

## 6. Revised work order and handoff hold

R0: reconcile this document + primary-source research into all canonical docs, validated feature
matrix, grammar and decisions. Preserve original reports; add corrections separately.
R1: confined worker + bounded host inference proof, broker authority and expected-head tests,
including negative controls. No real secret mounts; no global security weakening.
R2: isolated Hermes TUI widget and Desktop backend transport feasibility, code-host/harness
contract probes. No live plugin installation.
R3: present CLI/TUI/Desktop wireframes and material parity decisions for owner sign-off.
R4: issue a new pinned build handoff with phases, acceptance tests and explicit release holds.
Only then start implementation phases. Never hand the old pack to another builder as ready.

No new sudo change, paid service, credential creation or live deployment authorised by this
revision. The withdrawn container probe was later explicitly reauthorised and its bounded
result is recorded in `reports/M0-ROOTLESS.md`; further tests remain within that synthetic,
no-download/no-system-change scope. Code-Shipper product source remains unbuilt.
