# Local Executor Bridge v6

Status: side-by-side candidate; do not replace the installed v5 service until production acceptance passes.

## Scope

Local Executor Bridge is a least-privilege Mac execution bridge for authorised remote clients. It is deliberately not the conversation/work-thread continuity system. Conversation lifetime, provider handoff, work-thread persistence, and ChatGPT-side timeout orchestration belong to the separate successor project.

## Security model

- The allowed repository root is the ordinary remote authority boundary.
- Raw read-only and repository shells are sandboxed: writes are limited to their declared roots, network is denied, and reads elsewhere in the user home are denied.
- Broad `system` execution is outside that sandbox and always requires local operator approval.
- The workspace coordinator is privileged only for its own worktree/Git lifecycle. Remote `workspace exec` and integration validation payloads are sandboxed to the job worktree/Git metadata, have no network, and cannot read unrelated home files.
- High-impact command classification remains defence in depth; it is not the primary security boundary.
- Durable STARTED/FINISHED identity, process containment, output/runtime bounds, and no automatic replay of ambiguous mutations remain invariants.

## Wake lease

The daemon owns a single wake lease while real bridge work is active and for a configurable idle grace period (default: 3600 seconds) after work drains. The implementation uses `/usr/bin/caffeinate -i -w <daemon-pid>` on macOS: display sleep is not inhibited, and daemon death releases the assertion. A durable workspace waiting for another instruction does not by itself hold the lease. `health.json` exposes wake-lease state.

## Side-by-side deployment and cutover

V6 uses a distinct product identity (`Local Executor Bridge`), install/config/state paths, LaunchAgent label (`net.laurenzo.local-executor-bridge`) and mailbox. The v5 service remains live throughout staging. The v6 installer never retires v5 before v6 passes an end-to-end smoke test. Old LaunchAgents are retired only with the explicit `--retire-old-after-smoke` cutover option.

Production cutover gates:

1. full unit/static suite passes;
2. sandbox adversarial probes prove raw and workspace payloads cannot read unrelated home files, write outside permitted roots, or reach the network;
3. wake assertion appears under active work, display sleep remains permitted, idle grace releases it, and daemon death cannot orphan it;
4. separate-mailbox v6 end-to-end smoke test passes while v5 remains healthy;
5. client/skill mailbox identity is switched to the verified v6 mailbox;
6. only then retire both old LaunchAgent definitions;
7. verify v6 health and a final harmless request after retirement.
