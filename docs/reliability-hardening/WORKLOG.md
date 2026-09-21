# Worklog

- Reconciled Mac `main`, local HEAD, and `origin/main` at `7ff2da2618f68dd9891f012b7006ce0ff2842895`; working tree clean.
- Reproduced intermittent request delay while daemon stayed alive under LaunchAgent KeepAlive.
- Inspected production LaunchAgent: `RunAtLoad=true`, `KeepAlive=true`; process remained alive, so process supervision is not the primary fault.
- Inspected production stdout: repeated `rclone command timed out after 30s` on poll/request transfers and `5s` health upload failures across unrelated conversations.
- Inspected tracked `shell_bridge/bridge.py`: all rclone calls are subprocesses guarded by one global `_TRANSPORT_LOCK`; worker download/upload/delete operations therefore contend with poll and health.
- Located mature persistent `rclone rcd` transport in `src/llm_git_bridge/transport.py`, including private Unix socket, operation-specific timeouts, health/restart behaviour, safe read fallback, and no immediate fallback after ambiguous mutating timeout.
- Created and pushed durable workspace job `shell-bridge-reliability-20260920-a1`.
