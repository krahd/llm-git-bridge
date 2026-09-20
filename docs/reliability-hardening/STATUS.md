# Status

**Last verified checkpoint:** workspace branch `ai/workspace/shell-bridge-reliability-20260920-a1` created and pushed from canonical `main` `7ff2da2618f68dd9891f012b7006ce0ff2842895`; production logs and tracked source confirm serialized cold-rclone transport contention.

**Current phase/task:** implementation preparation — inspect focused tests/installer, then add persistent RC transport to shell bridge.

**Blocking issues:** none.

**Exact next action:** read `shell_bridge/tests/test_bridge_v5.py` transport-related tests and `shell_bridge/install.sh` packaging in bounded ranges; success condition is an exact minimal file-change plan for the first code patch.
