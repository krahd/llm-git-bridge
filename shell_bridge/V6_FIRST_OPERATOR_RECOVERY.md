# Bridge v6-first recovery: Mac operator package

**Status (25 October 2026): containment utility hardened and tested offline, but NOT accepted on the user's Mac. Do not treat an isolated unit-test pass as approval for production service changes.**

The included `bridge_v6_recovery.py` implements the *inspection and conditional containment* portions of issue #39. It does **not** perform the subsequent v5-to-v6 handover. The earlier `bridge_cleanup.py` is withdrawn.

## Read-only Mac Terminal preflight

Extract the archive or obtain the exact script from the reviewed GitHub PR. Run directly in Terminal, **never through the bridge**:

```sh
python3 ~/Downloads/bridge_v6_recovery.py --inspect > ~/Desktop/bridge-v6-preflight.json
```

Inspect the JSON. Exit code `3` means a HOLD. The inspection reads local launchd, process, journals, approvals and configured request folders on Google Drive via `rclone lsf` rooted at immutable Drive folder IDs. It does not execute, move or delete bridge requests. Do not share the JSON publicly: it contains service paths, PIDs, state paths and immutable mailbox identifiers (not command bodies or credentials).

Requires macOS, Python 3.10+, rclone, configured remote access, and a normal login Terminal process. No `sudo`.

## What the guard checks

- Explicitly pinned v5 *temporary* keeper and all recognised v6, staging, candidate and rollback LaunchAgents; unknown/loaded/unmanaged bridge processes block action.
- Exact script/config/process identities and locally owned non-group-writable registration/config files; symlinked path/ancestor and unexpected mailbox roots block.
- Children of the keeper **and** the targets, pending approval records (including malformed or nonfinite expiry), active and incomplete journal receipts.
- Complete bounded listings of the **pinned Drive requests folder IDs** for every active mailbox: any executable `.json` request or failed remote query blocks.
- Fingerprint time-bucket, recorded byte digests, and fresh inspection before applying and before each service action.

An exit code 0 from `--inspect` is **not** permission to run `--contain` automatically. It is one snapshot subject to concurrent changes and Drive eventual consistency; establish a maintenance window and operator approval first.

## Later: guarded containment only, not promotion

After explicit review of current local/Drive preflight and maintenance freeze, the separately authorised command would be:

```sh
python3 ~/Downloads/bridge_v6_recovery.py --contain EXACT_CURRENT_64_CHARACTER_FINGERPRINT
```

`--contain` refuses bridge-child invocation, recomputes the snapshot, disables and boots out exact obsolete LaunchAgent labels, verifies process exit, hard-links registration into a private archival directory without destination replacement, durably syncs it, removes the on-login registration, and verifies the temporary v5-only state. Configuration, Git work, historical receipts and approvals are preserved. A partial failure is **HOLD**: do not blindly rerun or reinstate another daemon. The report is under `~/Library/Application Support/llm-git-bridge/retired-launchagents/`.

The utility cannot make an atomic transaction out of live Google Drive operations and `launchctl`. In particular a request can arrive during the inspection-to-bootout interval; a maintenance freeze and independent acceptance remain necessary. No claim of exactly-once behaviour is made from this utility alone.

## Subsequent migration steps

1. Reconcile the historical production journal and approval state without deleting or replaying indeterminate work.
2. Qualify current v6 source, including required admission-digest hardening and exact-head native macOS CI.
3. Stage v6 without attaching a second executor to the production mailbox.
4. Perform a guarded exclusive one-owner v5-to-v6 handover.
5. Prove unique execution, native approval Allow/Reject, Git workspace operations, and exclusive restart after login/reboot.

Tracked at https://github.com/krahd/llm-git-bridge/issues/39 and draft PR #78.

## Mismatched historical LaunchAgent plist labels

A Mac operator preflight on 25 October 2026 stopped with:

```
HOLD: plist label differs:
net.laurenzo.local-executor-bridge-v6-staging.candidate-c90c9758c48345f6d9af26c64f580f6d64f66d7f
```

This is a **correct safety hold**: the filename and the plist's internal launchd `Label` disagree. It is not a verified inactive or safe-to-delete registration. macOS uses the internal `Label` for service identity, so do not blindly `bootout`, rename, remove, or disable the basename as if it were the actual service label. No cleanup action was performed.

The latest inspector returns a machine-readable **partial HOLD** JSON on stdout instead of an empty output file when parsing fails; `inspection_complete=false` and `containment_ready=false` prohibit using it as a valid preflight fingerprint. For a targeted **read-only** local diagnosis:

```sh
p="$HOME/Library/LaunchAgents/net.laurenzo.local-executor-bridge-v6-staging.candidate-c90c9758c48345f6d9af26c64f580f6d64f66d7f.plist"
/usr/bin/plutil -lint "$p"
/usr/bin/plutil -extract Label raw -o - "$p"
/bin/launchctl list | /usr/bin/grep -F 'local-executor-bridge-v6-staging' || :
```

Reconcile the internal label, file path, loaded launchd identity, daemon PID, mailbox/config, and possible future-login registration *before* any alteration. If it is an inactive stale plist, a separate audited operator-only quarantine procedure may be appropriate; otherwise resolve active ownership first.
