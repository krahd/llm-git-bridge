# Bridge v6-first recovery: operator package

## What this package does

`bridge_v6_recovery.py` is an out-of-band macOS utility for the **containment phase only** of the v6 migration. It does NOT promote v6. Its default mode is a read-only inventory. The optional guarded containment operation retires verified obsolete bridge LaunchAgents and leaves v5 as the sole **temporary** production owner until a newly qualified v6 can be promoted.

The previously supplied `bridge_cleanup.py` is withdrawn and must not be used.

## First action (safe read-only preflight)

1. Extract this archive to your Downloads folder, or download `bridge_v6_recovery.py` directly.
2. Open **Mac Terminal**, not a ChatGPT bridge request, and run:

   ```bash
   python3 ~/Downloads/bridge_v6_recovery.py --inspect > ~/Desktop/bridge-v6-preflight.json
   ```

3. Review or share the preflight JSON. Exit 3 means HOLD; no mutation is performed. Some paths and PID metadata may be sensitive; no command bodies or credentials are emitted.

The output includes a `fingerprint` tied to the exact launchd/config/process snapshot. The script reports `containment_ready` and reasons when it is not ready. A pending approval file can be expired, but is preserved regardless.

## Containment only after review

The user has authorised removing duplicate and obsolete services, but this package should **not** be run with `--contain` until a current preflight report and mailbox quiescence are independently reviewed. An accepted operator command would be:

```bash
python3 ~/Downloads/bridge_v6_recovery.py --contain EXACT_64_CHARACTER_FINGERPRINT
```

This disables and boots out exact non-v5 bridge services and archives their direct LaunchAgent plists outside the autostart folder, preserving configs and journals. It refuses unknown daemons, running children, active journal markers, incomplete STARTED records and live or unverifiable approvals. A partial operation is a HOLD; do not automatically rerun or restore.

## What remains after containment

- Verify the one-owner v5 temporary state.
- Resolve production journal and approval handover blockers without deleting history or replaying commands.
- Merge and qualify missing v6 safety changes (notably PR #76, open when this package was prepared).
- Stage a fresh trusted v6 build in isolation.
- Promote v6 through a separately guarded maintenance-window cutover, then verify approval, exactly-once request handling, and reboot persistence.

GitHub issue: https://github.com/krahd/llm-git-bridge/issues/39
