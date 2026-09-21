# Shell Bridge Reliability and Concurrency Hardening Plan

Objective: make the canonical v5 ChatGPT Shell Bridge reliable under heavy multi-conversation use while keeping GitHub canonical, Mac execution authoritative, and Google Drive transport-only. Many conversations must be able to read and work simultaneously without corrupting repositories or causing mailbox starvation/timeouts.

Canonical repository: https://github.com/krahd/llm-git-bridge
Canonical workspace job: shell-bridge-reliability-20260920-a1
Workspace resource: shell-bridge/reliability-hardening

Established evidence:
- Production launchd remains alive while the mailbox intermittently stops progressing.
- Production logs show repeated 30 s rclone poll/request timeouts and health-publication delays.
- shell_bridge/bridge.py serialises fresh rclone subprocess operations behind one global transport lock, so workers can starve poll/health.
- The repository already contains a mature persistent rclone rcd/Unix-socket transport pattern in src/llm_git_bridge/transport.py.
- shell_bridge/workspace.py already provides per-job branches/worktrees, pushed checkpoints, logical resources, overlap checks, and serialized integration.

Definition of done:
1. Control-plane Drive operations cannot be blocked behind a 30 s global transport lock; normal poll/health/read/write paths use bounded operation-specific transport and recover automatically.
2. Multiple independent bridge requests can run concurrently without transport starvation.
3. Read-only repository access remains cheap and concurrent.
4. Substantial repository mutation defaults to one durable workspace job per conversation/task, with its own branch/worktree and pushed checkpoints.
5. Different jobs in the same repo may work concurrently; repo identity alone is never a long-lived lock.
6. Integration alone is serialized per repo+target branch; stale bases, changed-path overlap, and logical-resource overlap are detected before canonical mutation.
7. Ambiguous transport writes are never blindly replayed; ambiguous Git mutations are reconciled against filesystem/remote state before retry.
8. Conversation/UI timeout recovery resumes exact durable jobs rather than creating duplicate work.
9. Canonical main is integrated, pushed, independently verified, installed runtime updated, one controlled restart succeeds, health advances, and live concurrent smoke tests pass.

Execution phases, deliberately micro-stepped:
A. Reconcile state and inspect only exact transport/workspace functions and relevant tests.
B. Add focused failing transport tests: persistent rcd with Drive-root ID pinning; short poll timeout; poll/health not starved by worker transfer; ambiguous-write safety; rcd restart/fallback.
C. Implement smallest shell-bridge transport adaptation by reusing the proven rcd design while preserving raw-shell protocol and Drive-ID pinning.
D. Run only the new transport tests; repair one failure at a time; checkpoint/push when green.
E. Add focused workspace concurrency tests: two jobs same repo/disjoint resources run concurrently; overlapping resource/path is detected at integration; integration lock serializes only canonical integration; pushed checkpoint recovery works after conversation loss.
F. Add/adjust the smallest workspace/client affordance needed to make isolated workspaces the default for substantial mutations while leaving read-only raw-shell access concurrent and cheap. Avoid hidden repo-wide locks.
G. Run workspace-focused tests one group at a time; repair one failure at a time; checkpoint/push.
H. Run shell_bridge test subsets, then the bounded full shell_bridge suite only after subsets pass.
I. Adversarial audit for starvation, duplicate execution, stale-base integration, overlapping edits, orphaned worktrees, unpushed checkpoints, unsafe retry, lock leakage, and launchd restart behaviour.
J. Mark ready and integrate through the workspace gate with validation; independently verify origin/main.
K. Deploy integrated shell_bridge/workspace runtime at a verified quiescent point; perform one controlled restart; verify installed hashes/version.
L. Live acceptance: health advances; lightweight requests complete; several concurrent read requests complete; at least two isolated workspace jobs can exist/work concurrently; no global mailbox stall; integration safety remains intact.
M. Final state checkpoint: STATUS COMPLETE, continuation says no pending work, all intended commits on canonical main.

Timeout invariant:
- one substantive bridge request at a time from this executor;
- command scope normally <=20 s; split uncertain operations further;
- exact-result check, then at most one health check before treating a request as pending;
- never resubmit an ambiguous mutation;
- no broad scans, redundant canaries, or repeated full suites;
- push a coherent workspace checkpoint before any context-heavy or deployment phase.

Rollback/recovery:
- workspace branch is durable on GitHub before integration;
- integration validates in an isolated worktree and pushes canonical state only after checks;
- installed runtime is changed only after origin/main verification;
- retain old installed runtime/hash until post-restart acceptance passes.
