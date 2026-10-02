# Mac Executor Bridge v6 hardening programme

Status: side-by-side candidate; v5 remains production until explicit cutover.

## Goals

1. Remove duplicate operational identity: one production daemon, LaunchAgent, mailbox owner, config and state tree after cutover.
2. Strengthen least privilege against a malicious or confused remote conversation.
3. Add a bridge-owned 60-minute idle wake lease.
4. Remove accidental keyboard activation from elevated-action approval.
5. Clean product naming/documentation without absorbing Conversation Harness successor responsibilities.

## Non-goals

No work-thread abstraction, conversation/provider persistence, browser handoff, cloud relay, iPhone coordinator, or ChatGPT-side timeout solution is implemented here.

## Zero-development-downtime migration

- Build on an isolated workspace branch.
- Keep installed v5 and its live mailbox untouched.
- Qualify v6 with an isolated runtime/state/config and candidate mailbox.
- Before cutover require v5 `active_requests=[]` and `started_without_finished=0`.
- Stage v6 production config first.
- Atomically boot out both obsolete v5 service labels and bootstrap the single v6 label against the existing production mailbox identity.
- Run one harmless production smoke request and verify result/health/wake state.
- If bootstrap or smoke fails before any mutating production request, boot v6 out and restore the previously running v5 LaunchAgent.

## Security acceptance gates

- repo write cannot escape its repository/worktree/Git roots;
- raw repo shell cannot reach the network by default;
- sandbox child does not inherit SSH agent/cloud/token environment;
- common credential paths are denied;
- system authority requires local approval;
- dialog has no default/focused action button and Space/Return cannot choose one;
- command/request bounds and STARTED/FINISHED replay semantics remain intact.
- workspace coordinator requests are exact direct-argv invocations; top-level shell chaining is rejected;
- coordinator-owned Git/state operations may use network/credentials, but remote-supplied workspace commands and integration validation run offline with credential environment stripped and worktree/Git-only writes.

## Wake acceptance gates

- idle bridge has no assertion;
- first pending/active request acquires one idle-sleep assertion;
- concurrent requests still produce one lease;
- when work drains, grace begins;
- new work resets grace;
- after 3600 seconds of continuous idleness the assertion disappears;
- display sleep is not inhibited;
- daemon death cannot orphan the assertion because caffeinate is bound to the daemon PID.
