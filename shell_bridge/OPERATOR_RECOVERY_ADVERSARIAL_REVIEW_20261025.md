# Operator containment utility adversarial review — 25 October 2026

**Scope:** draft PR #78, `shell_bridge/bridge_v6_recovery.py` and `tests/test_bridge_v6_recovery.py`. Review of the original 9-test version, subsequent safety changes, and offline execution of the hardened version. **Not a Mac production acceptance or v6 promotion.**

## Verdict
Original version: **reject for production**. Hardened version: **offline tests passed, Mac-local preflight and native qualification still required**. Do not treat a test pass as permission to stop services from the contested mailbox. The previous `bridge_cleanup.py` is withdrawn.

## Material defects in the original implementation and remediation

1. **Active work on the retained v5 keeper omitted** from containment gate. Updated to inspect both keeper and all target process descendants; active/incomplete local journal markers remain holds.
2. **No authoritative Drive request-queue check.** Added bounded, read-only rclone listings rooted on *pinned request folder IDs*, not names. Any `.json` entry, failed query, invalid remote, or malformed listing blocks. Included in preflight digest and repeated at mutation boundaries. This does **not** make Google Drive and launchd atomic.
3. **Unsafe string prefix checks and symlink traversal** for daemon/config/journal paths. Replaced with component-aware root containment, ancestor symlink checks, owner and group/world-write guards on registration/config/script, and executable hash binding.
4. **Stale/ambiguous approval and journal state.** Added rejection of non-finite/invalid approval deadlines, symlinked receipt markers and directories, bounded journal scanning, and explicit expiry counting while preserving all historical files.
5. **Time-of-check/time-of-use and service ancestry** lacked sufficient controls. Added bounded-time fingerprint, exact per-label rescans, and an operator process-ancestry veto for commands submitted through the bridge daemon.
6. **Archive write risks:** `os.rename` may overwrite a destination after the precheck, directory path could be symlinked, permissions may not be private, and metadata durability was incomplete. Replaced with private archive path validation, no-replace hard-link-and-unlink archival, directory `fsync`, content-hash verification and per-label append/fsync ledger. Any partial failure remains HOLD; no auto-rollback.

## Test evidence

- `python3 -m unittest discover -s bridge-recovery -p 'test_bridge_v6_recovery.py' -q` on isolated Linux container: **24 passing tests**.
- Test families: nominal dry-run, missing/stale/expired/malformed approval states, active target and keeper children, incomplete and symlinked journals, config/process identity mismatch, path prefix escape and ancestor symlink, insecure registration mode, unknown daemon, pinned remote queue lookup and unreadable/nonempty queue, expired preflight fingerprint, bridge-child veto, guarded no-overwrite/private archival in a mocked fixture.
- `python3 -m py_compile` and AST parse pass.
- Direct invocation with `--inspect` or `--contain` on Linux rejects safely with exit 2.
- GitHub branch source/test blobs independently re-fetched and confirmed equal to tested local Git blob hashes.
- GitHub Actions initial run 40175721313: bridge-v6-regressions passed, core job failed in pre-existing `test_installer_reservation.py::test_two_simultaneous_atomic_reservations_have_one_winner` with observed `[0,0]` instead of `[0,1]`; repeated on updated branch run 40176395894. Do not waive unexplained core failure. Native macOS execution of this utility is not established.

## Residual non-waivable gates

1. **No live Mac preflight has been obtained for the hardened version.** Prior Mac snapshots showed 4 running daemons, 17 loaded or parked LaunchAgents, 12 pending approval files and 850 historical journal integrity issues. Current state may differ.
2. **No atomic cutover guarantee:** Drive request arrival, launchd state and local journaling cannot be atomically locked by this utility. A maintenance freeze and independent ownership/queue verification are essential; refuse to stop anything if live commands or approval state are ambiguous.
3. **macOS-native filesystem/launchctl acceptance missing.** Verify actual identity, archive/fsync behaviour, PID exit and login persistence on the real Mac. CI unit tests are not a substitute.
4. **Historical non-replayable journal and approval holds remain.** The containment tool does not clean, replay, or certify them; qualification of a fresh v6 build and an exclusive v5-to-v6 handover are separate work.
5. **Approval UX symptom not independently reproduced with correlated request IDs.** Duplicate production ownership is verified and sufficient to warrant cleanup, but no claim that every doubled popup comes from that cause.

## Operator decision

Run a new **read-only** `--inspect` from a Mac Terminal that is not a child of the bridge. Review its holds and direct pinned remote listings; keep this PR draft. **Do not run `--contain` or attempt v6 promotion until Mac evidence, release CI, maintenance freeze and explicit acceptance are satisfied.** No Mac services were changed in this review.
