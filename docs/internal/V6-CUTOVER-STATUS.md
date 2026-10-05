# v6 cutover status

- Scope/source of truth: `krahd/llm-git-bridge`; GitHub `main` canonical, Mac workspace authoritative for local execution, v6 staging mailbox transport only.
- Cutover job: `bridge-v6-cutover-20261004-a1` (`shell_bridge/v6-cutover`).
- Base commit: `b0d08f77fd6e961e903f5ddce226dfab27ac0eb3`.
- Relevant prior v6 approval-policy checkpoint: `4015c39e8e36b483c67084aad82f1f7c76b140ab` (validated focused tests; not assumed integrated).
- Last verified runtime fact: earlier `v6-stage-candidate-20261004-2141` expired before execution; no staging install mutation occurred.
- v5 constraint: production v5 remains independently owned by its parallel conversation; this job must not restart/reconfigure/install/stop it before controlled cutover.
- Current phase: Phase 0 — persist audited plan and recovery state.
- In-flight ambiguous mutation: none.
- Blocker: none.
- Next bounded action after this checkpoint: Phase 1 authoritative parity audit of current main, prior v6 checkpoint, and relevant v5 reliability/correctness deltas; produce parity ledger and implement only missing/safe deltas.
- Success condition: plan/status checkpoint pushed; parity ledger evidence complete; no v5 runtime mutation.
