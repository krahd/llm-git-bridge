# Mac Executor Bridge v6

Mac Executor Bridge v6 is the hardened interim local executor used while the separate conversation-continuity project evolves. It is deliberately **not** a work-thread, conversation-lifetime, browser-handoff, or provider-continuity system.

## Security model

- One daemon owns one mailbox and one state tree.
- Ordinary remote shell requests are sandboxed and cannot write outside the selected Git repository/worktree.
- Raw repository shells are offline by default. Networked Git publication belongs to the trusted workspace coordinator or to an explicitly elevated action.
- Sandboxed child environments do not inherit SSH-agent sockets, cloud tokens, or arbitrary daemon environment variables.
- Common credential/key locations are denied to sandboxed remote shells.
- Broader `system` authority always requires local operator approval unless the local configuration explicitly disables the gate.
- The approval dialog is click-only: neither button is default or initially focused; Space/Return cannot approve or cancel it.

The shell-command classifier remains defence in depth. Filesystem sandboxing, environment minimisation, network denial, exact request identity and explicit elevation are the primary boundaries.

## Wake lease

When the daemon sees pending or active bridge work it acquires a macOS idle-sleep assertion using `/usr/bin/caffeinate -i -w <daemon-pid>`. After all work drains it holds the assertion for `wake_idle_grace_seconds` (default 3600 seconds), resetting the grace period when new work arrives. Display sleep is never inhibited. The `-w` binding ensures an unexpected daemon exit cannot leave a permanent caffeinate process.

`health.json` reports wake-lease state and remaining grace time.

## Scope boundary

Conversation/work-thread persistence, provider handoff, ChatGPT delivery-timeout recovery, browser automation and multi-device continuity belong to the separate successor to Conversation Harness. v6 only provides a safer, clearer local execution substrate.

## Migration

v6 is built and qualified side-by-side with v5. Development must not modify the installed v5 runtime. A candidate uses separate install/config/state paths, LaunchAgent label and test mailbox. Production cutover occurs only after the v6 unit, security, power-management and end-to-end gates pass and the v5 mailbox is quiescent.

## Qualification note

Because v6 is built through the live v5 bridge, tests that themselves invoke `sandbox-exec` cannot be executed inside that already-sandboxed build request (`sandbox_apply` rejects nesting). They remain in the suite and are run from the standalone v6 candidate LaunchAgent during side-by-side acceptance. This is a test-environment limitation, not a production exemption.

The normal installer never boots out v5. `cutover.sh` is a separate explicit final-step tool and names both obsolete v5 service labels.
