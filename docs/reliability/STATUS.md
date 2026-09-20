# Reliability Hardening Status

State: active
Job: `bridge-reliability-20260920-a1`
Base: `7ff2da2618f68dd9891f012b7006ce0ff2842895`

## Current diagnosis

The primary observed incident class is transport degradation, not daemon absence. launchd KeepAlive/RunAtLoad is present and the daemon remains alive. The runtime log contains repeated rclone timeouts. v5 puts polling, health publication, request download, result publication and deletion behind one global serial transport lock; slow worker transport therefore starves the control plane. Remote health can become stale while the process is alive and currently does not expose transport-waiting workers.

## Current phase

A — reproduce/measure and inspect reusable RC transport machinery already present elsewhere in the repository.

## Next action

Add a deterministic starvation regression test in the isolated workspace, then implement the smallest transport-scheduling change that makes the test pass without weakening durability semantics.
