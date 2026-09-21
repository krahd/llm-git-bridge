# WORKLOG

- 2026-09-20: Reconciled canonical checkout main and origin/main at 7ff2da2618f68dd9891f012b7006ce0ff2842895.
- Diagnosed repeated production stalls from daemon logs: repeated 30 s rclone timeouts on polls/request transfers and health publication delays while launchd daemon remained alive.
- Confirmed shell_bridge/bridge.py uses one global transport lock around fresh rclone subprocesses for polling and worker transfer operations.
- Confirmed canonical repository already has mature persistent rclone rcd transport in src/llm_git_bridge/transport.py with Unix socket, bounded operation-specific timeouts, safe read fallback, and ambiguous-write protection.
- Created and pushed durable workspace job shell-bridge-reliability-20260920-a1 on ai/workspace/shell-bridge-reliability-20260920-a1.
- Prior malformed read request was rejected before execution; no repository mutation occurred.
- Previous conversation timeouts motivated strict one-request-at-a-time orchestration and durable checkpointing.
- 2026-09-20: Scope expanded by user to simultaneous conversations with consistency. Plan now combines transport hardening with default isolated workspaces, concurrent reads/work, short serialized integration, overlap detection, durable recovery, and live concurrency acceptance.
