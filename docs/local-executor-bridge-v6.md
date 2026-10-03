# Local Executor Bridge v6 — canonical architecture

Status: canonical convergence target. The hardened v6 implementation in `shell_bridge/` is the only production bridge architecture. The `mac_bridge/` staging copy has been retired; protocol-v2 remains compatibility code, not a second daemon.

## Invariants

1. One production daemon owns one mailbox, request journal, active-process table, health surface, wake lease, and operator-approval gate.
2. Shell and Git are capabilities of that daemon. Git mutation uses the bundled trusted workspace coordinator rather than a separately running Git bridge.
3. GitHub is canonical shared repository state; Mac worktrees are authoritative local execution state; mailbox storage is transport only.
4. Remote input never expands its own authority. Local configuration plus resolved scope decides allow / local approval / reject.
5. Repository visibility is a non-waivable human-only publication boundary: agent requests may create repositories only explicitly private and must be rejected if they create a public repository or make one public.
6. Raw shell is least privilege. Generic reads are filesystem-and-network read-only; repository writes are worktree/Git scoped and offline by default.
7. The coordinator may use credentials/network only for its own fetch/checkpoint/push/integration lifecycle. Remote `exec` and validation payloads are sandboxed inside their worktree with network and credential inheritance denied.
8. Broad `system` execution always requires local approval. High-impact classifiers add an approval gate but are never the sole security boundary.
9. Approval presents semantic intent first (`explanation` + authority/effect), exact command details second, and fails closed. Approval has no keyboard-default action.
10. STARTED/FINISHED durability, bounded process groups/output/runtime, unique request IDs, and no automatic replay of indeterminate mutations remain mandatory.
11. Substantial Git mutation is durable: isolated workspace -> checkpoint/push -> ready -> integration validation -> canonical push -> independent remote verification.

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
6. production mailbox handoff while quiescent;
7. harmless production smoke;
8. retire obsolete shell/Git LaunchAgents;
9. verify v6 health and final request;
10. update client/skill mailbox/runtime identity.

If a pre-mutation cutover smoke fails, restore the previous service. Never run two consumers against the same live mailbox.

## Scope boundary

Conversation/provider persistence, WorkThreads, browser handoff, higher-level planning, and multi-device continuity belong to the separate work-continuity system. Local Executor Bridge supplies executor/workspace capabilities only.


## Production migration invariant

The v6 production cutover must not orphan durable workspace jobs. A fresh install uses the v6 state tree, but an upgrade from v5 stages the v6 LaunchAgent with the existing production state directory (`~/.local/state/chatgpt-shell-bridge`) and propagates that directory through `LOCAL_EXECUTOR_BRIDGE_STATE_DIR`. The v6 daemon trusts only the `workspace.py` shipped beside its own `bridge.py`; after a successful production smoke, a compatibility link at the historical coordinator path may point to that bundled coordinator for older clients. No stale v5 coordinator remains authoritative.

## In-place v5 to v6 production cutover

An upgrade that keeps the existing production mailbox must never start v5 and v6 against that mailbox at the same time. Stage v6 first, preserving the v5 durable workspace state, then perform the bounded service switch out-of-band (for example, from Terminal):

```bash
RCLONE_REMOTE=chatgpt-git-bridge: \
BASE_PATH="ChatGPT Shell Bridge" \
STATE_DIR="$HOME/.local/state/chatgpt-shell-bridge" \
ALLOWED_ROOT=/Users/tom/tom-repos \
bash shell_bridge/install.sh --stage-only

bash shell_bridge/cutover.sh
```

`cutover.sh` refuses to run as a request through the bridge it is replacing. It requires the production mailbox to be quiescent, stops the old consumers before starting v6, runs `doctor` and an end-to-end harmless production request, rolls the old active service(s) back if qualification fails, and only after success archives old LaunchAgents and points the historical coordinator path at the bundled v6 `workspace.py`.
