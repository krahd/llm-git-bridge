# Prepare a v6 replacement without stopping production

> **Superseded (2026-10-25).** Retained as history only. Production installs,
> upgrades and recovery use [`V6_PRODUCTION_OPERATIONS.md`](V6_PRODUCTION_OPERATIONS.md).


Use the new `stage_v6_upgrade.sh` from a **verified local source checkout**
only after a source config and exact intended commit have been selected. The
script is a staging mechanism, **not** a duplicate-owner remediation command.

It requires `SOURCE_CONFIG_FILE` (the existing production
`~/.config/<service>/config.json`), `EXPECTED_SOURCE_COMMIT` (the exact
40-character Git revision checked out locally) and a fresh `UPGRADE_SUFFIX`.
The script reads the source's remote, mailbox name, allowed root, existing
durable state and shell; it creates a new immutable candidate under its own
fresh install/config/LaunchAgent paths.

`install.sh --upgrade-stage-only` uses the existing remote marker and
existing request/results folder IDs *read-only*, validates all nine source
identity fields against the production configuration, copies source policy into
a separate candidate config, builds its own approval app, and produces a
candidate install manifest. Its unbootstrapped LaunchAgent plist is parked at
`$INSTALL_DIR/launchagent.plist`, **not** in
`~/Library/LaunchAgents` where macOS might automatically load it at login.
It performs **no** `rclone mkdir`, upload or
remote deletion, and does **not** create, chmod or write the production
durable state directory. It does not `bootstrap`, `bootout`, `kickstart`
or otherwise activate a LaunchAgent. A failed/partial local candidate
installation is a HOLD; never overwrite a prior candidate to retry.

This does **not** assert that the running daemon is the sole mailbox owner,
that the remote request queue is empty, that journal receipts are reconciled,
or that a cutover can proceed. The separate guarded `upgrade_v6.sh`
continues to require one verified source owner, a complete journal audit,
remote quiescence, source/candidate identity, and independent acceptance.
The current Mac's conflicting loaded services and historical indeterminate
production completion require reconciliation before any service stop.

CI covers source identity, privacy and linked-path guards, non-activation
staging ordering, shell syntax and the v6 pre-stop/rollback contracts.
Real authenticated macOS staging and production acceptance are separate
operational gates.
