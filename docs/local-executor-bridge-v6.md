# Local Executor Bridge v6 — canonical architecture

Status: canonical convergence target. The hardened v6 implementation in `shell_bridge/` is the only production bridge architecture. The `mac_bridge/` staging copy has been retired; protocol-v2 remains compatibility code, not a second daemon.

## Invariants

1. One production daemon owns one mailbox, request journal, active-process table, health surface, wake lease, and operator-approval gate.
2. Shell and Git are capabilities of that daemon. Git mutation uses the bundled trusted workspace coordinator rather than a separately running Git bridge.
3. GitHub is canonical shared repository state; Mac worktrees are authoritative local execution state; mailbox storage is transport only.
4. Remote input never expands its own authority. Local configuration plus resolved scope decides allow / local approval / reject.
5. Raw shell is least privilege. Generic reads are filesystem-and-network read-only; repository writes are worktree/Git scoped and offline by default.
6. The coordinator may use credentials/network only for its own fetch/checkpoint/push/integration lifecycle. Remote `exec` and validation payloads are sandboxed inside their worktree with network and credential inheritance denied.
7. Broad `system` execution always requires local approval. High-impact classifiers add an approval gate but are never the sole security boundary.
8. Approval presents semantic intent first (`explanation` + authority/effect), exact command details second, and fails closed. Approval has no keyboard-default action.
9. STARTED/FINISHED durability, bounded process groups/output/runtime, unique request IDs, and no automatic replay of indeterminate mutations remain mandatory.
10. Substantial Git mutation is durable: isolated workspace -> checkpoint/push -> ready -> integration validation -> canonical push -> independent remote verification.

## Permission resolution

`write_scope` remains the compatibility wire field:

- `auto`: choose the least authority compatible with the request;
- `read_only`: no writes, no network;
- `repository`: worktree/Git-root writes only, raw shell network denied;
- `system`: full local-user authority after explicit operator approval.

An exact trusted `workspace.py` invocation is not a generic shell escape. The bridge recognises only the installed coordinator and known subcommands. The coordinator then applies its own inner sandbox to remote command/validation payloads.

Future root/capability policy may add richer local declarations, but it must compile to the same resolved-effect model rather than introduce a second executor. Existing protocol-v2 repository capabilities are migration input, not a reason to retain a second daemon.

## Approval semantics

Every request requires a bounded non-empty `explanation`. If local approval is required, the operator sees the caller's intended action and authority/effect first, followed by request ID, working directory, and exact command in selectable details.

Approval is local and pre-STARTED. Decline/timeout is terminal `rejected`; no child process exists and replay cannot create a side effect. The dialog is foreground-visible and neither button may be default/focused.

## Wake lease

The daemon owns one macOS idle-sleep assertion while bridge work is pending/active and for the configured idle grace period (default 3600 s) after work drains. It uses `caffeinate -i -w <daemon-pid>` so display sleep remains allowed and daemon death releases the assertion.

## Migration / cutover

Development and qualification may stage v6 beside the currently installed service, but the final state is singular. Cutover gates:

1. unit/static suite;
2. adversarial sandbox tests;
3. wake-lease tests;
4. isolated v6 end-to-end smoke;
5. production mailbox handoff while quiescent;
6. harmless production smoke;
7. retire obsolete shell/Git LaunchAgents;
8. verify v6 health and final request;
9. update client/skill mailbox/runtime identity.

If a pre-mutation cutover smoke fails, restore the previous service. Never run two consumers against the same live mailbox.

## Scope boundary

Conversation/provider persistence, WorkThreads, browser handoff, higher-level planning, and multi-device continuity belong to the separate work-continuity system. Local Executor Bridge supplies executor/workspace capabilities only.
