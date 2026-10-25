# Local Executor Bridge v6 — production operations (canonical)

This is the single supported procedure for installing, upgrading,
consolidating and recovering the production bridge on a Mac. It supersedes
`V6_TO_V6_UPGRADE.md`, `V6_UPGRADE_STAGING.md`, `docs/V6-ONE-RUN-RELEASE.md`,
`cutover.sh`/`upgrade_v6.sh`/`v6_release_once.sh` for production use, and the
draft containment utility of PR #78. Those remain in the tree as history.

## The one-owner invariant

Exactly **one** LaunchAgent — label `net.laurenzo.local-executor-bridge` —
runs exactly one `bridge.py daemon` for the production mailbox. There is no
v5 fallback, no parallel staging daemon on any mailbox, and no per-candidate
LaunchAgent label. One native approval app (the release's own bundle)
serves that daemon.

Why this is enforced in code, not just policy (issue #39):

* v5 and early v6 builds locked `instance:<bridge_instance_id>`; later v6
  builds locked `drive-mailbox:<root id>`. A v5 and a v6 daemon on the same
  mailbox held *different* lock files and both executed requests. The
  daemon now holds the union of every historical identity (`instance:`,
  `drive-mailbox:`, `mailbox:<remote>|<base>`) in the launchd `TMPDIR`,
  `/tmp`, and the durable `~/.local/state/local-executor-bridge-locks/`.
* macOS cleans temporary directories; an unlinked lock file lets a new
  claimant lock a fresh inode. The daemon re-verifies its lock inodes every
  poll, re-asserts a cleaned path, and if another process holds it prints
  `MAILBOX_LOCK_LOST`, stops admitting, drains admitted work and exits 75.
* STARTED was written with a replacing rename after a separate existence
  check. Each request is now processed under an exclusive per-request claim
  (`<state>/request-claims/<id>.lock`) and STARTED is created with no-replace
  `link(2)`; a duplicate consumer never executes, publishes or signals.
* Startup containment no longer signals a process group recorded before the
  current boot (its PGID may belong to an unrelated process).

## Installed layout

```text
~/Library/LaunchAgents/net.laurenzo.local-executor-bridge.plist   the only bridge plist
~/.local/share/local-executor-bridge-releases/<commit>/            immutable release
~/.config/local-executor-bridge-releases/<commit>/config.json      release config
~/.local/share/local-executor-bridge  -> releases/<commit>         canonical path (symlink)
~/.local/share/chatgpt-shell-bridge/workspace.py -> canonical      legacy client shim only
~/.local/state/chatgpt-shell-bridge/                               production journal (kept)
~/.local/state/local-executor-bridge-locks/                        durable mailbox locks
~/Library/Application Support/llm-git-bridge/consolidation/<run>/  run evidence + archives
```

The daemon trusts only the `workspace.py` beside its own resolved
`bridge.py`, so both client paths resolve to the running coordinator.

## Upgrade or consolidation procedure

Run every step in an interactive Terminal on the Mac. **Never** submit any
of these steps through a bridge mailbox; the controller refuses to run as a
descendant of `bridge.py daemon`.

1. **Source.** Use a clean worktree at the exact `origin/main` commit that
   passed CI on macOS and Linux. Run both suites locally:
   `PYTHONPATH=src python3 -m unittest discover -s tests` and
   `python3 -m unittest discover -s shell_bridge/tests`.
2. **Stage an inert release** (reads Drive, writes nothing to the journal,
   the mailbox or `~/Library/LaunchAgents`):

   ```bash
   C=$(git rev-parse HEAD)
   INSTALL_DIR=~/.local/share/local-executor-bridge-releases/$C \
   CONFIG_DIR=~/.config/local-executor-bridge-releases/$C \
   PLIST=~/.local/share/local-executor-bridge-releases/$C/launchagent.plist \
   STATE_DIR=~/.local/state/chatgpt-shell-bridge \
   SOURCE_CONFIG_FILE=<current production config.json> \
   RCLONE_REMOTE=<remote> BASE_PATH="<mailbox folder name>" ALLOWED_ROOT=<root> \
   bash shell_bridge/install.sh --upgrade-stage-only
   ```

3. **Plan.** Write an operator-local plan JSON (never commit it):

   ```json
   {"keeper_label": "net.laurenzo.local-executor-bridge",
    "root_id": "<root>", "requests_id": "<requests>", "results_id": "<results>",
    "state_dir": "/Users/<you>/.local/state/chatgpt-shell-bridge",
    "release_dir": "/Users/<you>/.local/share/local-executor-bridge-releases/<commit>",
    "release_commit": "<commit>",
    "canonical_install_link": "/Users/<you>/.local/share/local-executor-bridge",
    "legacy_coordinator_dirs": ["/Users/<you>/.local/share/chatgpt-shell-bridge"]}
   ```

4. **Census** (read-only): `python3 shell_bridge/v6_consolidate.py census`.
   Review every registration *by declared label* (file names are not
   identities), loaded/disabled state, PIDs, request children, held lock
   files, journals, approvals and pinned-ID queues. Note the fingerprint.
5. **Migrate:** `python3 shell_bridge/v6_consolidate.py migrate --plan P --fingerprint F`.
   It refuses unless the live census is unchanged, the release matches its
   manifest and the planned mailbox/journal identity, no request command is
   running, no approval is actionable and the production journal has no
   in-flight STARTED. It then:
   * copies every plist/config into the run directory;
   * disables and boots out idle respawning labels first, then running
     non-keeper owners, then the old keeper; quits old approval apps;
   * proves no bridge process, loaded label or lock holder remains and
     re-checks the journal; writes the historical indeterminate ledger;
   * archives every registration with no-replace link-then-unlink;
   * points the canonical path and legacy shim at the release, writes the
     single plist, enables and bootstraps it;
   * waits for PID-bound health naming the release commit with a matching
     manifest, and verifies exactly one loaded label, one daemon PID and
     held durable locks.
6. **Acceptance** on the installed service (see below).

### Failure semantics

Every action is logged (fsynced) in `events.jsonl` and every filesystem
change in `recovery-actions.jsonl` *before* it is relied upon. Before the
`point_of_no_automatic_return` event nothing has started; after it the
controller never restarts an old owner — an ambiguous result is a HOLD for
reconciliation, never two executors. `v6_consolidate.py rollback --run DIR`
reverses the recorded filesystem actions only when no bridge is loaded and
the journal shows no in-flight request; services stay disabled and exactly
one owner must then be bootstrapped deliberately.

### Historical journal evidence

Old `indeterminate` FINISHED receipts are terminal: the daemon publishes them
again on a matching replay but never re-executes them. They are listed in
the run's `indeterminate-ledger.json` with each FINISHED SHA-256 and are
never deleted or rewritten. Reconcile their external effects from Git/remote
state when needed; do not fabricate completed receipts.

## Acceptance checklist (installed service)

* exactly one `bridge.py daemon`; `launchctl list` shows only the keeper;
  every obsolete label is `disabled` in `launchctl print-disabled`;
* `health.json`: `publisher_pid` = launchd PID, `build_source_commit` = the
  release, `build_code_integrity` = `matches_manifest`;
* a unique harmless request completes once; resubmitting the same file
  republishes the identical stored result without a second STARTED;
* native approval Allow, Reject and timeout; one window per request;
* sandbox denial (write outside the repository / network in read-only);
* timeout containment of a child process group;
* workspace coordinator create/exec/ready through the legacy client path;
* `launchctl kickstart -k` restart keeps one owner and the same locks.

## Troubleshooting

* `another shell-bridge instance already holds the instance lock` in a
  respawn loop: some other process holds one of the mailbox identities.
  Find it with `lsof +D ~/.local/state/local-executor-bridge-locks` and the
  census; never delete lock files to "free" the mailbox.
* `MAILBOX_LOCK_LOST` (exit 75): a lock path was replaced and another
  process holds the new inode. Run the census before anything else.
* Stale `health.json`: advisory only. Check launchd and the exact result.
