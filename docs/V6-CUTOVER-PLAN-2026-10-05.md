# Local Executor Bridge v6 — audited completion and cutover plan

Date: 2026-10-05
Owner: v6 cutover durable workspace `bridge-v6-cutover-20261004-a1`
Canonical repository: `krahd/llm-git-bridge`
Target: one production Local Executor Bridge v6, with v5 retained unchanged until v6 production acceptance passes.

## Authority and non-negotiable invariants

1. GitHub `main` is canonical shared repository state. The durable v6 workspace is the mutation surface until integration.
2. v5 and v6 remain operationally independent during development. Do not restart, reconfigure, install over, retire, or otherwise disturb v5 merely to advance v6.
3. v6 never requires identity authentication: no Touch ID, password, `LAContext.evaluatePolicy`, `deviceOwnerAuthentication`, Secure Enclave `userPresence`, or equivalent. A deliberate Allow/Reject click is the human authorization boundary when approval is actually required.
4. Approval is effect/risk based, not scope-label based. Ordinary reversible repository work must not prompt. Approval is reserved for dangerous, destructive, outside-repository, security/credential, control-plane, or materially irreversible effects.
5. The menu-bar approval queue is the durable human control surface. Popups may attract attention but cannot be the only way to find or decide pending approvals.
6. Preserve v5 reliability semantics unless v6 deliberately supersedes them with tested stronger behavior: bounded concurrency, process-group containment, crash/journal recovery, request/result limits, Drive transport bounds, health semantics, wake lease, durable workspace coordination, GitHub remote verification, installer/update/rollback safety.
7. No v5 delta is copied blindly. Final parity is computed against current canonical `main` immediately before v6 integration; reliability/correctness fixes are ported, obsolete v5 popup/auth semantics are not.
8. Production cutover is reversible until post-cutover acceptance is complete. Legacy services are retired only after production v6 smoke and rollback validation pass.

## Verified starting state

- Dedicated v6 staging daemon/mailbox is live and independent of v5.
- Existing v6 approval-policy checkpoint: `4015c39e8e36b483c67084aad82f1f7c76b140ab`; its focused suite passed 15/15.
- Dedicated durable cutover job exists. It was created from then-current canonical `main` and has a pushed checkpoint; its `interrupted` state reflects a prior bounded command failure, not lost work.
- Current staging still reports `operator_confirmation_mode=dialog`; this is not final acceptance evidence for the new menu-bar queue build.

## Phase 0 — recover and inventory the durable cutover workspace

Actions:
- Resume the existing cutover job and inspect only its current branch/worktree/checkpoint state.
- Fetch/reconcile current `origin/main` without overwriting local work.
- Inventory v6-related files, installer/runtime paths, staging LaunchAgent, approval helper, menu-bar app/helper, health schema, tests, and documentation.

Gate:
- Worktree is recoverable and clean or intentionally dirty with understood changes.
- Remote checkpoint exists and is verified.
- Current canonical main SHA recorded.

## Phase 1 — exact v5 → v6 parity ledger

Actions:
- Compare current canonical `main`, v6 cutover branch, and any still-unintegrated v5 branch/worktree changes that are authoritative and safe to inspect.
- Classify every relevant delta in `shell_bridge/`, workspace coordinator, installer/LaunchAgent, tests, and docs as:
  - already inherited;
  - must port (reliability/correctness/safety);
  - superseded by v6 design;
  - v5-only/obsolete;
  - unresolved and requiring a test before decision.
- Persist the ledger in this document or a sibling parity document with commit/path provenance.

Mandatory v5 capabilities to preserve or prove equivalent:
- bounded concurrent requests and process-group supervision;
- STARTED/FINISHED journal and indeterminate recovery;
- timeout/restart descendant containment;
- request/command/stdin/stdout/stderr limits;
- bounded rclone/Drive transport and exact-ID recovery discipline;
- filesystem sandboxing and home/network restrictions for ordinary requests;
- trusted workspace coordinator semantics and remote-ref verification;
- health/staleness semantics and wake lease;
- installer/update/rollback and legacy-service retirement guards.

Do not port:
- Touch ID/password/identity-authentication behavior;
- one-request-only modal UX as the durable approval surface;
- `system` scope as an automatic reason to prompt;
- v5-specific workaround code made irrelevant by the v6 architecture.

Gate:
- No unclassified reliability/safety delta remains.

## Phase 2 — finish v6 approval policy and menu-bar queue

Required behavior:
- Safe repository work: no approval.
- Safe read-only work: no approval, except deliberately sensitive secret/credential boundaries.
- A request that asks for broad/system scope but whose resolved effect is safe is downgraded to the least authority needed; it does not prompt merely because of the requested label.
- Dangerous/destructive/outside-repo effects require approval and receive only the exact authority for that approved request.
- Approval UI is click-only Allow/Reject; no identity authentication.
- Persistent menu-bar icon shows pending count and complete queue.
- Queue entries show plain-language explanation first, with command/path/details available.
- Missing/dismissing a popup never loses the request; it stays in the queue until decided/expired.
- Multiple conversations aggregate safely in the queue.
- Decisions are request/nonce/payload bound and cannot authorize a different request.
- Expired/rejected decisions fail closed.

Tests:
- classifier table tests for safe/dangerous/ambiguous effects;
- downgrade tests for over-broad `write_scope=system` requests;
- no-auth primitive regression scan;
- multi-request queue ordering/decision isolation;
- expiry/restart/duplicate decision tests;
- UI helper relaunch and menu-bar persistence tests where automatable.

Gate:
- Focused approval/queue suite green.
- Static scan finds no forbidden authentication primitives in the live v6 approval path.

## Phase 3 — v6 staging deployment and acceptance

Actions:
- Build/install a uniquely named stage-only candidate from the verified checkpoint.
- Do not modify v5 production services.
- Launch candidate staging service only after verifying its paths/mailbox/state/LaunchAgent are isolated.
- Run acceptance matrix:
  1. safe repo edit/build/test → executes without approval;
  2. safe request mislabeled `system` → downgraded, no approval;
  3. read-only diagnostics → no approval;
  4. outside-repo harmless write → approval required because boundary is crossed;
  5. destructive Git/system/control-plane effect → approval required;
  6. Allow → executes exactly once, no authentication;
  7. Reject/expiry → no execution;
  8. multiple pending approvals → all visible in menu-bar queue;
  9. daemon/helper restart with pending approvals → no accidental execution/loss;
  10. timeout/descendant containment and crash recovery remain correct.

Gate:
- Entire staging matrix passes from authoritative results/health/state, not merely process exit codes.

## Phase 4 — full regression and adversarial audit

Run repository test suites plus targeted adversarial checks for:
- sandbox escape/home/secret reads;
- network access from ordinary repository scope;
- shell quoting/compound commands that may bypass effect classification;
- destructive command aliases/wrappers (`sh -c`, `bash -c`, `python -c`, `find -exec`, `xargs`, scripts);
- symlink/path traversal outside allowed roots;
- Git destructive operations and force push;
- control-plane/LaunchAgent/process/service mutations;
- command timeout, daemon restart, STARTED-without-FINISHED recovery;
- concurrent requests and concurrent repo jobs;
- output limit and Drive/rclone failure behavior;
- stale health heartbeat semantics;
- installer rollback and partial-install recovery;
- approval request replay/tampering/nonce mismatch.

Audit rule:
- A classifier cannot claim to prove arbitrary shell safety from command text alone. Unknown/opaque commands receive sandboxed least authority; if their requested effect needs authority outside the sandbox, they require explicit approval rather than optimistic execution.

Gate:
- No high-severity unresolved failure; any expected environment-only failures are documented with a narrower independent validation that proves the relevant behavior.

## Phase 5 — final canonical reconciliation and integration

Actions:
- Fetch latest `origin/main` after parallel v5 work is done or quiescent enough for one final reconciliation.
- Compute exact changed-path/commit delta since the cutover job base.
- Port only still-missing v5 reliability/correctness fixes; preserve v6 approval semantics.
- Re-run affected focused tests and regression suite.
- Mark ready and integrate through the workspace coordinator concurrency gate.
- Independently verify the pushed canonical remote SHA and intended v6 content.

Gate:
- Canonical `main` contains the complete v6 implementation and final v5 parity fixes with no unresolved overlap.

## Phase 6 — production cutover with rollback preserved

Preconditions:
- production mailbox quiescence/known in-flight request handling;
- final v6 build derived from verified canonical commit;
- rollback package/config/service state captured;
- v5 remains available until production acceptance completes.

Actions:
- Install v6 production service using the canonical production mailbox/configuration strategy defined by the repo.
- Do not retire v5 first.
- Run production smoke: health, read-only request, normal repo mutation, trusted coordinator operation, approval-required operation, rejection, timeout containment, Drive result publication.
- Verify menu-bar queue and no-auth behavior on the actual production build.
- Exercise rollback path without losing request/journal state, then restore accepted v6 if rollback test succeeds.

Gate:
- Production v6 passes all smoke and rollback checks.

## Phase 7 — retirement and documentation

Only after Phase 6 passes:
- retire/disable obsolete v5/legacy LaunchAgents and duplicate live mailboxes as specified by the canonical installer;
- archive rather than destroy useful forensic state where appropriate;
- remove obsolete authenticated approval app/source/install artifacts that could be mistaken for the live path;
- update README, architecture, installation, security/approval policy, migration/rollback, troubleshooting, health schema, and operator UX documentation;
- document that agents/conversations should use v6 and that approval is effect-based and click-only;
- verify clean install/update path from repository documentation.

Completion gate:
- one intended production bridge remains;
- production health/current request path is v6;
- no obsolete service can race for the production mailbox;
- canonical GitHub `main` and docs match deployed reality;
- rollback/recovery instructions are tested and truthful;
- no unresolved ambiguous request or unpushed repository work remains.

## Adversarial audit of this plan

Failure mode: porting v5 continuously while it is still changing creates divergence.
Mitigation: one final canonical reconciliation near integration; v5 remains operationally untouched during v6 development.

Failure mode: command-text classification gives false confidence for arbitrary shell composition.
Mitigation: least-authority sandbox is primary; the classifier only grants broader authority for recognized effects. Opaque commands cannot silently escape the sandbox.

Failure mode: menu-bar UI becomes a new authorization bypass.
Mitigation: queue decisions remain request/nonce/payload bound, atomic, expiring, and fail closed; UI is only the human decision surface.

Failure mode: removing authentication accidentally removes deliberate consent for truly dangerous effects.
Mitigation: dangerous effects still require an explicit Allow click; only identity authentication is prohibited.

Failure mode: staging install disturbs v5 or production state.
Mitigation: unique stage paths/label/state/mailbox; `--stage-only` and isolation verification before launch.

Failure mode: cutover retires the only working bridge before v6 is proven.
Mitigation: v5 retirement occurs only after production v6 smoke plus rollback acceptance.

Failure mode: passing focused tests hides regressions in mature v5 reliability behavior.
Mitigation: explicit parity ledger plus full regression/adversarial phase before canonical integration.

Failure mode: concurrent main changes overwrite or are overwritten by v6.
Mitigation: durable workspace, pushed checkpoints, `ready` reconciliation, serialized integration gate, remote verification.

Failure mode: apparent success is based on stale health or transport acknowledgement.
Mitigation: acceptance uses exact results, local runtime state, and verified remote refs; health heartbeat staleness is advisory only.

## Current execution status

- [ ] Phase 0 recovered and inventoried
- [ ] Phase 1 parity ledger complete
- [ ] Phase 2 approval policy/menu queue complete
- [ ] Phase 3 staging acceptance complete
- [ ] Phase 4 full regression/adversarial audit complete
- [ ] Phase 5 canonical integration verified
- [ ] Phase 6 production cutover + rollback accepted
- [ ] Phase 7 retirement/docs complete
