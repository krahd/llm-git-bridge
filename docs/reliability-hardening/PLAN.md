# Shell Bridge Reliability Hardening

## Objective
Eliminate intermittent alive-but-not-progressing behaviour in the ChatGPT Shell Bridge while preserving protocol-1 raw-shell semantics, Drive-ID pinning, replay/crash safety, bounded concurrency, and LaunchAgent supervision.

## Confirmed failure
Production logs show repeated 30 s rclone subprocess timeouts for mailbox polling and request/result transfers, plus 5 s health publication timeouts. All shell-bridge transport calls share one global lock; concurrent workers therefore queue behind slow rclone subprocesses and can starve polling and health while the daemon process remains alive.

## Plan
1. Replace per-operation cold rclone subprocess use with a private persistent `rclone rcd` control-plane path over a user-only Unix socket, retaining bounded subprocess fallback.
2. Preserve `--drive-root-folder-id` pinning for both RC and fallback paths.
3. Use short operation-specific control-plane timeouts; never immediately fallback after an ambiguous mutating RC timeout.
4. Keep a single mailbox owner; local request execution may remain concurrent. Transport failure must leave requests/results recoverable and retryable.
5. Add focused transport, fallback, health/restart, replay, and installer/runtime tests; run shell-bridge focused suite before integration.
6. Checkpoint and push the workspace; integrate through the workspace gate; independently verify GitHub `main`.
7. Update the installed runtime only from verified canonical `main`, restart once at an observed quiescent point, then run small live mailbox probes and inspect logs/health.

## Acceptance gates
- No unbounded wait behind the transport lock; poll/list control path is bounded to a few seconds.
- Persistent RC path is primary when healthy; subprocess path remains safe fallback.
- Root-folder-ID trust boundary is unchanged.
- Existing protocol/replay/crash semantics remain green.
- New reliability tests reproduce and prevent the diagnosed failure class.
- Canonical GitHub `main` contains the tested fix and is independently remote-verified.
- Installed runtime matches canonical code after controlled restart; live probes complete and health continues advancing.

## Rollback
All edits occur in workspace job `shell-bridge-reliability-20260920-a1`. Canonical `main` stays untouched until validated integration. Deployment preserves the previous installed runtime until canonical integration passes; any ambiguous restart/update is reconciled from actual runtime/Git/health state before retry.
