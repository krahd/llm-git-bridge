# Reliability Hardening Worklog

## 2026-09-20

- Reconciled local `main` and GitHub `origin/main` at `7ff2da2618f68dd9891f012b7006ce0ff2842895`.
- Verified launchd service `com.tom.chatgpt-shell-bridge` is loaded, running, RunAtLoad and KeepAlive.
- Observed daemon alive for roughly 22 hours, so process-exit supervision is not the missing mechanism.
- Reproduced a period where requests sat in the mailbox and Drive health stopped advancing; the same requests later completed without a restart.
- Inspected runtime logs: repeated 30-second rclone poll/request-transfer timeouts and 5-second health-publication timeouts affect many unrelated conversations.
- Inspected v5 transport: all rclone calls are serialized by `_TRANSPORT_LOCK`; poll/health and worker download/upload/delete share it.
- Identified architectural amplification: up to 20 workers can queue multiple slow transport operations behind one lock, starving poll/health for far longer than a single timeout.
- Verified earlier crash-containment test failure involving `PermissionError` was already fixed and is not the present root cause.
- Created durable workspace job `bridge-reliability-20260920-a1`, resource `reliability/shell-bridge`.
