# Explicit v6-to-v6 handover (source qualification only)

`shell_bridge/upgrade_v6.sh` is the explicit v6-source variant of the
guarded cutover engine. It is **not** a general service-cleanup tool.
The original `cutover.sh` continues to default to v5-first cutover.

## Preconditions (must all be established before attempting)

- One and only one loaded and running production v6 owner; no other loaded
  or parked bridge LaunchAgent claims the source mailbox, request/results
  folders or journal, and no globally ambiguous loaded ownership.
- Current production heartbeat is fresh, v6, with a PID equal to the
  recorded LaunchAgent source PID, no active requests, and no pending approvals.
- Exact staging installation, immutable install-manifest file hashes and
  expected 40-hex Git revision are independently verified.
- The staged candidate has a **different, unused v6-specific LaunchAgent
  label**, but identical production mailbox root/instance/request/results
  identities and **exactly the same durable journal state directory**.
- Local durable request receipts, including all historical entries, verify
  without indeterminate completions, missing STARTED/FINISHED evidence or
  unbounded audit failures. The production remote request mailbox is
  independently and completely confirmed empty.
- The local operator-approval queue is independently confirmed absent or
  empty **before stop, after stop and before any old-v6 restoration**. Linked,
  unreadable and expired-but-retained approval records do not count as empty;
  the heartbeat's `active_requests` field alone does not prove this.
- The controller is an interactive Mac Terminal, never a request processed
  by the bridge being replaced or by the unsafe staging mailbox.

The source transition first inspects all directly installed LaunchAgent
plists for a *future-login* claimant to the production mailbox. It must find
exactly one old source and no dormant alternative that would reactivate after
login. Before source bootout it reversibly archives that old plist in a
private `retired-v6-<source-commit>` directory, while retaining all binaries,
configuration and journals. The controller performs a second mailbox drain
and a second journal integrity and approval-queue check **after** the old
owner stops and **before** the candidate starts.

Only after the candidate publishes exact-build/PID-bound fresh health does
the controller atomically register its already-staged plist under the direct
`~/Library/LaunchAgents` directory and independently recheck exclusive
ownership before a smoke request. No file is overwritten. An ambiguous
post-admission failure quarantines that new registration and leaves the old
archived with a durable hold; it does not restart an old daemon that might
duplicate effects. A proven failure *before* new admission may restore the
old plist after rechecking source journal, approval queue and loaded owners.

The first successful smoke produces **provisional** v6 service continuity
with a recorded startup-registration plan, not permanent retirement or a
tested reboot. The journal receipt explicitly records `reboot_verified=0`.
End-to-end native Allow/Reject, SSH/pinned-terminal scope, crash containment,
and actual logout/login restart behavior must still be accepted separately
before declaring durable production retirement.

## Operator input contract (do not run against the current Mac state)

After separate clean staging and independent acceptance, the eventual
operator invocation requires explicit `LOCAL_EXECUTOR_LABEL` (a fresh
`net.laurenzo.local-executor-bridge-v6-*` label), `NEW_PLIST`,
`CONFIG_FILE`, `INSTALL_DIR`, `LEGACY_STATE_DIR` (the production state),
and `EXPECTED_SOURCE_COMMIT` (the exact 40-hex staged revision).
Do not supply a previously loaded candidate, staging mailbox configuration,
or a separate new state directory.

No optional source-version flag is required at the entrypoint: it explicitly
selects version 6 and refuses bridge-originated execution. It is not a
fallback to the old v5-first release launcher.

## Known unresolved Mac blockers

The last operator audit found two live production and two live staging
claimants, thirteen additional loaded/parked LaunchAgents, and at least one
historical indeterminate production completion. Staging has empirically
executed the same request more than once. Under these conditions the
handover must **refuse to stop any service**; preserve all history.
Do not use it to fix ownership conflicts automatically.

GitHub CI/source tests establish source-level safety properties, not real
authenticated production acceptance. Any later live handover still needs
explicit proof of exclusive ownership, no duplicate effects, correct installed
build/PID, operator approval behavior, remote request delivery, and rollback
before legacy retirement.
