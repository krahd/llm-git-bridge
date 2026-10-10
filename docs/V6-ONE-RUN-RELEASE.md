# v5 to v6: one-invocation guarded local release

## Scope

The supported operator entrypoint is `shell_bridge/v6_release_once.sh`. Run it
from a **clean, isolated worktree** at the exact audited canonical release
commit. Do not run `git pull` in a dirty local checkout, and do not install over
the existing v5 binary/config.

This is one local invocation, not an unattended or unconditionally successful
cutover. No finite test suite can guarantee that external Google Drive,
LaunchAgents, or the native approval GUI will behave identically on the user's
Mac. The entrypoint holds on real uncertainty instead of coercing migration.

## Gates in execution order

1. Validate the clean Git snapshot, run all Python and shell tests, inventory
   every loaded bridge-related LaunchAgent and its actual pinned mailbox, and
   refuse unknown/multiple live owners.
2. Install and run a uniquely named isolated candidate in its own Drive mailbox;
   verify exact source SHA, manifest integrity, live launchd PID, fresh heartbeat,
   and a hash-bound read-only result under the expected sandbox.
3. Require **two actual native approval actions** on a SHA-pinned, read-only
   helper: first **Deny**, then **Approve**. Timeout, implicit rejection, GUI
   failure, approval replay, and mismatched request hashes cannot qualify.
4. Stage the same source commit for the existing v5 production mailbox **without
   starting it**. Pin the existing production root, requests/results IDs, allowed
   root, and preserved v5 state directory.
5. Present one explicit **MIGRATE** confirmation immediately before stopping v5.
   `cutover.sh` rechecks fresh quiescence, pending approvals, unresolved STARTED
   work, mailbox emptiness, and all real loaded production consumers. It stops
   only recognized consumers of the target production mailbox and starts v6.
6. Require hash-bound production read-only smoke and an additional native
   production approval before accepting the provisional handover.
7. Re-audit loaded LaunchAgents after cutover and require exactly one owner\n   of the production mailbox; any reloaded legacy or unknown consumer holds.\n8. Archive only the production v5 LaunchAgent plists that were actually stopped,
   after verifying v6 remains loaded. Keep v5 journals, configs, Git worktrees,
   rollback evidence and all unrelated staging services intact. The archive
   prevents those specific legacy production plists being auto-loaded at the
   next user login.

The produced `v6-release-acceptance.json` records both what passed and what
remains unverified. **Live crash/replay fault injection is not asserted** merely
because regular CI and normal operation passed.

## Failure handling

- Before stopping v5: a failure leaves production untouched; a partially
  created isolated candidate or staged production installation may remain.
  Reconcile it before retrying; never delete the old state or blindly replay a
  request with a new ID.
- After stopping v5 but before v6 starts: the cutover script attempts to
  restore exactly the previously loaded consumers. If restoration is incomplete,
  preserve its reconciliation marker.
- Once v6 may have admitted a request: an ambiguous failure **holds both
  consumers** and records manual journal/mailbox reconciliation instructions.
  It must not automatically restart v5, which could duplicate external effects.
- If verification after a provisional production switch fails, retain v6 and
  all rollback evidence. Inspect reality before a later action; no blind
  rollback or service retirement.
- The acceptance/archival step is reversible, not deletion. A partial archive
  is an explicit hold requiring reconciliation before another attempt.
- Other old staging LaunchAgents remain loaded until their own requests,
  approvals and journals are inspected. Do not retire unrelated provider
  sessions based solely on historical label names.

## Operational preconditions

Requires a macOS user session with `git`, `python3`, `rclone`, `launchctl`
and the full macOS Swift SDK. The existing v5 config and the established
`chatgpt-git-bridge:` rclone remote must remain accessible. The production
root is currently pinned to `1PRsQHgVsgXhIYT_8akGFRGdhdlw9Lmk6` to prevent
accidental adoption of an unrelated mailbox.

Three native GUI decisions are required (Deny, Approve, Approve), plus one
terminal confirmation for the consequential production switch. All other
ordinary staging and testing actions proceed without repeated operator
confirmations. **Do not run this script through the production bridge
it intends to stop.**

After the GitHub PR has been integrated and a canonical commit independently
verified, use a **single terminal paste** that fetches that SHA into a distinct
release worktree and invokes `bash shell_bridge/v6_release_once.sh`.

Do not claim full ecosystem migration or retire other staging instances until
their independent mailbox/journal ownership and provider routing are reconciled.
