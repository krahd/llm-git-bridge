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

## Required pinned SSH acceptance (added before v5 sunset)

The operator must provide `V6_SSH_POLICY_DIR` in the same terminal invocation.
This is an explicit prerequisite for retirement, not a request for additional
privileges. The directory must already contain a secure operator-owned
`ssh-policy.json` declaring a host, user, port and owner-only private key,
plus its corresponding pinned `known_hosts`. The installer receives that
directory via `REGISTER_PINNED_SSH_POLICY_DIR`, SHA-pins the helper and
host-key material, and preauthorizes only fixed read-only `status` and
`identity` actions. The full one-run controller then sends a *single* status
request through isolated v6 and production v6, using exact request hashes
and no-prompt authorization. A missing profile, altered key, unavailable host,
or ambiguous remote result holds rather than retiring v5.

This is an intentionally minimal first SSH capability. It does not claim
arbitrary interactive remote shell access or unrestricted SSH deployment.
Additional host/operation profiles require explicit policy design and
installation before v5 parity can be asserted. Do not run the older
unqualified release command; it lacks the operator-pinned SSH profile.

## Single-run interactive SSH enrollment

If `V6_SSH_POLICY_DIR` is not provided, the same release invocation now runs
`setup_v6_pinned_ssh.py` before any staging or production mutation. It prompts
once for a pinned SSH hostname, account, port, existing owner-only identity key,
and the already trusted `known_hosts` file. It displays the previously trusted
SSH host-key fingerprint(s), and **requires the explicit confirmation** `PIN
SSH` before writing a separate operator-owned SHA-bound policy directory.

The wizard does not call `ssh-keyscan`, use automatic trust-on-first-use, or
connect to a remote SSH endpoint. If the selected host is not already present
in the Mac's trusted host-key database, enrollment holds before staging so the
operator can verify the host key independently. Existing enrollments are never
overwritten; the operator can point `V6_SSH_POLICY_DIR` at a verified existing
policy on a later safe run.

This keeps the release at one shell invocation, with one-time host consent,
two deliberate native approval tests, and a separate consequential `MIGRATE`
authorization. No routine SSH status call requires another popup.

## Deliberate limited-SSH release decision

Passing the pinned `status` probe does not imply general SSH/SCP/SFTP/RSYNC
support. Before stopping v5, the release controller states this limitation and
requires the explicit one-time phrase `MIGRATE LIMITED SSH` to accept it.
If previous production workflows depend on arbitrary outbound SSH or transfers,
**do not authorize the cutover**; keep v5 running until corresponding bounded
SSH capabilities and live tests are available. This is a business-functionality
and safety gate, not a request to weaken the sandbox.

## Inbound SSH requirement and limits

Inbound SSH is macOS Remote Login, not the bridge transport. Before v6
production staging, the one-run controller reads Remote Login status and checks
that the current account belongs to the restricted SSH access group. If this
is not yet configured, the operator can use macOS System Settings > General >
Sharing > Remote Login, restrict access to specific authorized users, and
recheck from the same running script. No script enables Remote Login, installs
a service, opens a firewall port or weakens host authentication.

Before production switching, a fresh 48-hex-digit challenge is generated in
a private local state directory. An operator must establish a genuine SSH login
to the Mac **from a separate device**, execute the displayed witness command
in that authenticated session, and let the running controller verify its
identity, peer address and freshness. A missing or expired witness blocks
production handover. This is a separate-client SSH session diagnostic; it does
not independently establish public-internet routing, nor can local source
tests prove access through an off-site firewall/VPN.

The SSH login account can operate through ordinary macOS SSH authorization;
it does not gain additional arbitrary command authority inside the AI bridge.
The existing outbound pinned SSH helper still permits only previously
approved fixed read-only actions without repeated bridge prompts.

The operator should use key-based SSH authentication, restrict Remote Login
to necessary users and private/VPN network paths where practicable, and verify
host keys independently. Remote Login should never be automatically exposed to
the public internet by a migration script.

## Latest source audit findings

A fresh pass identified two broken release canaries that prior tests missed:
native approval omitted the bridge-required durable operation ID, and outbound
SSH smoke generated a request ID with a literal trailing dollar sign.
Both were corrected and tested against the actual bridge admission rules.
The pinned OpenSSH helper now excludes system-wide host-key trust, DNS host-key
verification and automatic host-key updates. The installed-build manifest,
daemon integrity identity and cutover manifest now include the SSH helper
SHA-256. These are source-level corrections; live Mac acceptance is still
required before declaring either direction of remote SSH operational.

## One Mac invocation from an existing dirty checkout

The currently known Mac checkout contains unpublished work on a non-main
branch. Running the prior release script there would fail its clean-source
check, and resetting, cleaning, or force-checking out main would be unsafe.
Use the operator-only source launcher added in
`shell_bridge/v6_launch_once.sh`, **without modifying the existing checkout**.

After reviewing the canonical remote `main`, execute one interactive command
in a Mac Terminal:

```bash
git -C /Users/tom/tom-repos/projects/llm-git-bridge fetch origin refs/heads/main:refs/remotes/origin/main && /bin/bash -c "$(git -C /Users/tom/tom-repos/projects/llm-git-bridge show origin/main:shell_bridge/v6_launch_once.sh)" -- /Users/tom/tom-repos/projects/llm-git-bridge
```

The launcher validates the canonical GitHub origin and exact fetched commit,
creates a private detached worktree under
`~/.local/state/bridge-v6-release-sources/<source-sha>`, checks the source
is clean and identical to fetched `origin/main`, then hands off to the existing
one-run guarded release controller. It never resets, cleans, stashes, checks
out, commits or overwrites the original Mac checkout.

A partial or interrupted release source remains for reconciliation; the
launcher refuses to reuse an existing source directory. A successful source
preparation **does not** mean a successful cutover. Native approvals, any
operator-required SSH enrollment and separate-device inbound SSH witness
remain unavoidable live checks. Do not automate these approvals.

For source qualification only, `V6_RELEASE_DRY_RUN=1` exercises creation
of an isolated source worktree without launching the release controller.
No dry-run output claims production acceptance.

## Local Git hook isolation during source preparation

A Git checkout's `.git/hooks/post-checkout` directory is local-only state,
not controlled by canonical GitHub `main`. Preparing a new detached worktree
can otherwise execute a stale, unpublished local hook before any migration
approval. The isolated source launcher forces `core.hooksPath=/dev/null` for
its `git worktree add` invocation so the release source does not execute
those local checkout hooks. Mac regression coverage installs a deliberately
side-effecting local test hook and confirms it was not invoked.

This prevents one known local code-execution path during source preparation.
It does not claim that arbitrary Git filters, system-wide executable
configuration or third-party credential helpers have been audited or that
a GitHub checkout is a sandbox. Production activation remains guarded.
