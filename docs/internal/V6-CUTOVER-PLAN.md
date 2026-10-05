# Local Executor Bridge v6 convergence and production cutover plan

Status: execution plan
Owner: v6 cutover workspace
Scope: converge v6 with all relevant v5 reliability/correctness work, validate approval UX/policy, cut over production safely, and retire superseded bridge services only after acceptance.

## Non-negotiable requirements

1. v5 and v6 work independently until the controlled convergence/cutover gate. Do not restart, reconfigure, install over, stop, or otherwise interfere with the live v5 runtime while v5 remains production.
2. v6 never requires user identity authentication for an approval: no Touch ID, password, `LAContext.evaluatePolicy`, device-owner authentication, Secure Enclave `userPresence`, biometric policy, or equivalent. The explicit Allow/Reject click is the human authorization boundary.
3. Approval is exceptional, not a generic privilege tax. Routine reversible repository work and ordinary read-only diagnostics run without approval. Approval is required only when the concrete effect is materially dangerous, crosses the repository authority boundary, or is not safely undoable/recoverable.
4. A requested broad scope is not itself sufficient reason to approve. v6 classifies the actual command/effect and narrows safe operations to the least authority necessary.
5. The durable human approval surface is a persistent menu-bar queue. Popups/notifications may attract attention but cannot be the sole durable access path.
6. GitHub is canonical repository state. Mac worktrees are authoritative for current local execution state. Drive is transport only.
7. Substantial repository work occurs in isolated workspace-coordinator jobs; integration is conflict/reconciliation gated and remotely verified.
8. No legacy service is retired until final production v6 smoke, rollback proof, and repository/documentation reconciliation pass.

## Source-of-truth inputs

- Canonical GitHub `main` at the start of each integration gate.
- The v6 approval-policy checkpoint `4015c39e8e36b483c67084aad82f1f7c76b140ab` and its remote workspace branch.
- Current v6 staging runtime/health and staging mailbox.
- Current v5 implementation only as evidence for parity/reliability features. v5 runtime is read-only to this project until retirement.
- Repository tests, installer scripts, workspace coordinator, status/architecture docs, and handoff records.

## Phase 0 — durable recovery state and plan persistence

Target:
- Create/resume one isolated `shell_bridge/v6-cutover` workspace from fresh canonical `main`.
- Persist this plan in the repository (under `docs/internal/` unless current repo structure establishes a more canonical v6 location).
- Persist a compact status/recovery record identifying base, checkpoint, current phase, blockers, and exact next action.

Acceptance:
- Plan and status are committed and pushed on the v6 cutover job branch.
- No v5 runtime mutation occurred.

## Phase 1 — authoritative parity audit: v5/main/v6

Purpose: determine what v5 reliability/correctness work v6 must preserve, without blindly copying v5 UX/auth design.

Audit families:
- request validation/protocol limits;
- concurrent admission and `max_active_requests`;
- STARTED/FINISHED journal semantics, indeterminate recovery, duplicate/replay prevention;
- process-group ownership, timeout cancellation, descendant containment, daemon-restart recovery;
- output/stdin/request/command limits;
- filesystem sandbox and home-read/network restriction semantics;
- rclone/Drive timeouts and mailbox identity pinning;
- health publication and stale-heartbeat semantics;
- wake lease/caffeinate lifecycle;
- durable workspace coordinator, checkpoint/push/reconciliation/integration/remote verification;
- installer/update/stage-only/rollback/legacy-service retirement behavior;
- recent v5 bugfixes since the v6 cutover base.

Port rule:
- Port or preserve reliability/correctness deltas not already present in v6.
- Do not port obsolete v5 approval UX, authentication flows, or `system => approval` semantics.
- If v5 and v6 solve the same requirement differently, test the requirement rather than mechanically cherry-picking implementation.

Acceptance:
- A repo-resident parity ledger maps each relevant v5 feature/delta to `already_present`, `port`, `superseded_by_v6`, or `not_applicable`, with evidence/tests.
- All required `port` items are implemented in the cutover workspace and tested.

## Phase 2 — approval effect/risk classifier hardening

Required behavior:
- Read-only under allowed roots: no approval.
- Routine repository/worktree edits, tests, builds, commits, branches, coordinator operations, and ordinary recoverable Git activity: no approval.
- Requests that merely ask for `system` but are concretely safe: automatically narrow to repository/read-only sandbox, no approval.
- Outside-repo mutations: approval unless explicitly trusted/local policy makes them safely bounded and reversible.
- Destructive/non-recoverable operations: approval (examples: destructive recursive deletion, dangerous system/config mutation, credential/security mutation, destructive Git history operations/force push, deletion of valuable repos/branches/remotes).
- Sensitive credential/secret reads are denied or explicitly approval-gated according to policy; raw request/journal material must never embed secrets.
- Unclassifiable potentially dangerous commands fail closed rather than silently gaining broad authority.

Acceptance tests:
- Matrix covering safe repo/read-only, safe request with overbroad scope, outside-repo write, destructive command, network/control-plane mutation, secret-sensitive access, and trusted coordinator paths.
- No false approval for normal repository workflows in the acceptance matrix.
- No privilege expansion caused solely by declared scope.

## Phase 3 — approval UX/menu-bar queue and no-auth guarantee

Required UI:
- Persistent menu-bar icon, unobtrusive when idle.
- Visible count/badge when approvals are pending.
- Menu exposes the complete pending queue.
- Each item leads with plain-language explanation; command/path/technical details are inspectable.
- Per-item Allow and Reject.
- Missed/dismissed popup does not lose the request; queue remains authoritative.
- Expired/decided items leave Pending and may appear in bounded history/audit UI.
- Multiple conversations aggregate into the same queue without modal collisions.
- App relaunch/daemon restart preserves or safely reconstructs pending state.

No-auth regression guarantee:
- Static/runtime tests fail if approval code imports/uses LocalAuthentication, `LAContext`, device-owner authentication, Touch ID/biometry, Secure Enclave user-presence requirements, password authentication, or equivalent identity-auth prompts.
- Allow click writes only the request-bound decision necessary for that exact request.

Acceptance:
- Queue works with multiple synthetic pending requests.
- Allow/Reject decisions bind to exact request ID/nonce/payload digest and cannot be replayed to another request.
- No identity authentication appears before or after Allow.

## Phase 4 — staging build/install and acceptance

Rules:
- Use isolated v6 staging mailbox/config/state/install paths.
- `--stage-only` must not cut over production or retire any live service.
- Staging installation itself is outside-repo mutation and may legitimately require one explicit approval.

Staging acceptance suite:
1. health/identity/folder IDs/version correct;
2. sandbox available and fail-closed for non-system execution;
3. safe `system`-requested command narrows and runs with no approval;
4. dangerous/outside-repo mutation enters menu-bar queue;
5. Allow executes exactly once without authentication;
6. Reject does not execute;
7. expiration does not execute;
8. daemon/helper restart does not lose or duplicate pending approval;
9. concurrency and independent requests work while an approval is pending;
10. timeout kills/contains descendants and records terminal/indeterminate state correctly;
11. output/request limits enforced;
12. coordinator create/exec/checkpoint/ready/integrate dry acceptance succeeds;
13. v5 production runtime remains unchanged.

## Phase 5 — full repository regression and adversarial audit

Run the repository's complete relevant test suite(s). Categorize any failures:
- regression caused by cutover work -> fix;
- pre-existing/environmental -> prove with fresh-base comparison or focused reproduction; do not mask;
- obsolete test contradicting accepted v6 architecture -> update with documented rationale.

Adversarial checks:
- path traversal/symlink escape;
- shell wrapping/compound command classifier evasions;
- destructive command aliases and common variants;
- command substitution/redirection/pipelines;
- false-positive approval burden on common workflows;
- approval replay/spoof/race/stale decision;
- queue concurrency and crash recovery;
- stage-only cannot mutate production mailbox/LaunchAgent;
- rollback artifact is usable.

Acceptance:
- Full applicable suite green, or every non-green test has a verified non-regression disposition documented in the status record.

## Phase 6 — fresh-main reconciliation and canonical integration

Before integration:
- Fetch latest GitHub `main` through coordinator.
- Re-run parity audit for commits landed after the cutover job base, especially v5 reliability/install/recovery changes.
- Reconcile changed-path/resource overlaps explicitly; never mark a real overlap reconciled without inspecting it.
- Re-run focused and full validation after reconciliation.

Acceptance:
- Coordinator says ready with no unresolved overlap.
- Integration validation succeeds in detached integration worktree.
- Canonical `main` push succeeds and GitHub remote SHA is independently verified.

## Phase 7 — production cutover with rollback proof

Preconditions:
- v6 staging acceptance complete;
- canonical integration verified;
- production mailbox is quiescent enough for controlled switch;
- rollback command/artifact/path verified before switching.

Cutover:
- install/start production v6 using intended canonical mailbox identity;
- do not retire v5 yet;
- run production smoke covering read-only, repository mutation, coordinator workflow, concurrency, timeout/recovery, safe overbroad-scope downgrade, and dangerous approval queue;
- verify no authentication prompt;
- verify repository work can commit/push/remote-verify normally.

Rollback proof:
- demonstrate that production routing/service can be returned to the prior known-good bridge state without data loss; if the proof requires an actual reversible switch, perform it in a bounded controlled window and then return to v6.

Acceptance:
- production v6 passes smoke after final switch;
- no unresolved STARTED requests/approval ambiguity attributable to cutover;
- rollback remains documented and viable.

## Phase 8 — retire superseded services and clean up

Only after Phase 7 acceptance:
- retire obsolete v5/legacy LaunchAgents/mailbox consumers specified by architecture;
- preserve necessary journal/audit evidence; do not delete ambiguous or recovery-relevant state;
- remove obsolete authenticated approval app/source/install remnants that could accidentally be launched;
- keep only canonical production bridge plus intentionally retained staging/recovery assets;
- update docs/README/architecture/status/install/uninstall/migration guidance to match reality;
- document no-auth approval rule, risk/effect classifier, menu queue, and rollback.

Final acceptance:
- one canonical production bridge architecture;
- GitHub docs and code match installed reality;
- production health current;
- no unintended legacy consumer active;
- v5 reliability features required for safety are present or superseded with passing tests;
- v6 approval UX/policy matches the non-negotiable requirements;
- final canonical GitHub SHA independently verified.

## Adversarial plan audit

The plan is invalid and must stop before cutover if any of these occur:
- v5 runtime would need to be changed before controlled cutover;
- a change is based on conversation memory rather than current repo/runtime evidence;
- a broad `system` request automatically bypasses classification or always prompts;
- approval requires identity authentication;
- a popup can be dismissed such that an approval becomes unreachable;
- a pending approval can execute after expiry/restart/replay without a fresh bound decision;
- full-suite regressions are waived without evidence;
- integration overwrites concurrent v5/main work rather than reconciling it;
- production switch occurs before rollback is known viable;
- legacy services are retired before production acceptance.

## Completion definition

Work is complete only when Phases 0–8 have either passed or an irreducible external/user-only blocker is recorded with exact recovery state. A branch checkpoint, staging pass, integration commit, or first production smoke alone is not completion.
