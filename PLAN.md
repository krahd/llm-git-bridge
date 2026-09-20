# Shell Bridge Reliability Hardening Plan

Objective: make the canonical v5 ChatGPT Shell Bridge reliable under concurrent multi-conversation load, eliminating repeated manual restart requests caused by transport stalls while preserving raw-shell semantics, Drive-ID pinning, crash/replay safety, and bounded concurrency.

Canonical repository: https://github.com/krahd/llm-git-bridge
Canonical workspace job: shell-bridge-reliability-20260920-a1
Resource: shell-bridge/reliability-hardening

Known evidence:
- Production v5 remains alive under launchd KeepAlive but repeatedly suffers 30 s rclone poll/request timeouts and 5 s health-upload timeouts.
- shell_bridge/bridge.py serialises every rclone subprocess behind one global transport lock; worker downloads/uploads/deletes therefore block polling and health.
- The same repository already contains a mature persistent rclone rcd/Unix-socket transport with short operation-specific timeouts, safe read fallback, ambiguous-write handling, and RC health/restart logic in src/llm_git_bridge/transport.py.
- Production repo main and origin/main reconciled at 7ff2da2618f68dd9891f012b7006ce0ff2842895 before workspace creation.

Plan:
1. Persist/reconcile execution state and workspace.
2. Inspect only the shell-bridge transport/lifecycle code and relevant tests.
3. Add focused failing tests for persistent RC transport, short poll timeout, non-blocking health/poll under worker transport activity, and safe ambiguous writes.
4. Implement the smallest transport adaptation in shell_bridge, preserving Drive root ID pinning and subprocess fallback.
5. Run focused tests; repair defects in microsteps.
6. Run shell_bridge test subsets, then full shell_bridge tests if bounded.
7. Checkpoint and push the workspace branch.
8. Mark ready and integrate through workspace gate into main with validation.
9. Independently verify origin/main.
10. Deploy the integrated runtime only at a quiescent point, perform one controlled restart, then verify live health and lightweight mailbox behaviour without redundant canaries.
11. Final adversarial audit: no 30 s control-plane blocking path, no unsafe write replay, no Drive-ID regression, no unpushed changes, canonical main verified.

Rollback/recovery: workspace branch is pushed before integration; installed runtime update occurs only after main is verified; ambiguous mutations are inspected before retry.

Completion gate: tests pass; main contains the fix; origin/main independently verified; installed runtime matches integrated source; controlled restart succeeds; live health advances and bounded request/result flow succeeds; STATUS is COMPLETE.
