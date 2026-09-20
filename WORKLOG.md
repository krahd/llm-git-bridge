# WORKLOG

- 2026-09-20: Reconciled canonical checkout main and origin/main at 7ff2da2618f68dd9891f012b7006ce0ff2842895.
- Diagnosed repeated production stalls from daemon logs: repeated 30 s rclone timeouts on polls/request transfers and 5 s health publication timeouts while launchd daemon remained alive.
- Confirmed shell_bridge/bridge.py uses one global transport lock around fresh rclone subprocesses for polling and worker transfer operations.
- Confirmed canonical repository already has mature persistent rclone rcd transport in src/llm_git_bridge/transport.py with Unix socket, bounded operation-specific timeouts, safe read fallback, and ambiguous-write protection.
- Created and pushed durable workspace job shell-bridge-reliability-20260920-a1 on ai/workspace/shell-bridge-reliability-20260920-a1.
- Prior malformed read request was rejected before execution; no repository mutation occurred.
- Previous conversation timed out before executor state files were persisted; resumed with smaller steps.
