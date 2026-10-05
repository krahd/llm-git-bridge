# v6 parity and migration ledger

Authoritative comparison date: 2026-10-04/05. Canonical comparison target is `origin/main` at cutover base `b0d08f77fd6e961e903f5ddce226dfab27ac0eb3`; unpublished local `main` is deliberately excluded until published or otherwise reconciled.

## Preserve: already present on canonical main

These v5-derived operational properties are already implemented/tested in the canonical v6 codebase and are requirements, not items to re-port from old branches:

- concurrent request admission with a bounded `max_active_requests` ceiling;
- STARTED/FINISHED journal semantics and non-replay of ambiguous/incomplete requests;
- process-group ownership, timeout termination, descendant containment, and restart containment;
- bounded request/command/stdin/stdout/stderr and explicit `output_limited`/`timed_out` reporting;
- macOS filesystem sandboxing with separate repository/read-only/system authority and home/network restrictions;
- bounded rclone transport and Drive root/folder pinning;
- health publication, stale-heartbeat semantics, and active/pending state reporting;
- wake-lease/caffeinate lifecycle;
- bundled durable workspace coordinator with isolated worktrees, checkpoints, readiness/reconciliation, integration, and GC;
- staged install, manifest verification, transactional/out-of-band cutover, rollback/preservation semantics, and legacy retirement gates.

These are validated by current code/tests rather than by requiring historical v5 branch tips to be ancestors.

## Port/reconcile into v6

1. `4015c39e8e36b483c67084aad82f1f7c76b140ab` — effect-based approval classification and persistent menu-bar approval queue. Reconcile against current canonical main rather than overwriting v5 popup fixes wholesale.
2. Authentication-free approval invariant from `d7aec44...`: approval is a deliberate Allow/Reject decision, never identity authentication. Add active-path regression guards for `LocalAuthentication`, `LAContext`, `deviceOwnerAuthentication`, Secure Enclave `userPresence`, Touch ID/password flows.
3. Preserve any current-main v5 request-authoring/protocol compatibility fixes that are generic transport correctness, especially mandatory explanation/request-shape compatibility. Do not import v5-specific modal implementation details.
4. Before final integration, reconcile any newer canonical `origin/main` delta once. Unpublished local-main changes are not authoritative and must never be overwritten or absorbed speculatively.

## Supersede / do not port

- v5 popup implementation as the v6 control surface;
- one-request modal-only approval UX;
- `system` scope as a sufficient reason by itself to ask for approval;
- LocalAuthentication / Touch ID / password / device-owner authentication;
- Secure Enclave `userPresence` approval keys or any identity-presence requirement;
- authenticated staging-repair bootstrap (`bridge_repair.swift`) as a required v6 path;
- v5 hotfix installers whose only purpose is preserving/fixing the legacy popup.

## Current canonical gaps blocking v6 acceptance

- canonical `origin/main` still contains an authenticated native Swift approval path (`approval_gui.swift`) and authenticated staging-repair path (`bridge_repair.swift`);
- canonical `bridge.py` still maps explicit `write_scope=system` directly to `confirmation_category=system_write` before effect classification;
- canonical install currently builds/links the native approval app with LocalAuthentication;
- effect-based/menu-bar checkpoint `4015c39...` is pushed but not integrated or installed;
- staging is still running the older implementation;
- the final cutover must wait for fresh-main reconciliation, full regression, staging acceptance, rollback proof, and production smoke.

## Concurrency note

At the audit, local `main` was `ebe631c93d4cd3f97495bea8200c8c208fa3bc37` while canonical `origin/main` remained `b0d08f77fd6e961e903f5ddce226dfab27ac0eb3`. The unpublished local changes overlap approval-policy files. They belong to parallel work and are protected: this job will neither reset nor consume them. Final integration will reconcile only authoritative published state.
