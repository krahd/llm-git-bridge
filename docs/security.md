# Security model

The bridge deliberately exposes less protocol surface than a shell or a general-purpose Git token. It is nevertheless an automation system that can execute locally configured validation commands against remotely supplied patches, so its trust boundaries need to be explicit.

## Allowed by default

- read selected tracked text working-tree files through filtered snapshots;
- create/edit/delete regular repository files through a validated patch;
- create/update branches under the configured safe prefix;
- request symbolic command names that the user has explicitly configured locally;
- request no more than 16 configured command executions in one transaction;
- create commits.

## Not allowed by the remote protocol

- supply arbitrary command argv or shell text;
- push unless it has been enabled locally for that repository and explicitly requested by the transaction;
- merge, force-push, choose a different remote/refspec, mutate repository administration settings, or export credentials;
- branch names outside the configured safe prefix;
- patching when tracked local modifications are present;
- applying a transaction whose base SHA is stale;
- create, modify, or delete symlinks/submodules;
- touch protected CI/automation paths such as `.github/workflows/`, `.github/actions/`, `.gitmodules`, `.circleci/`, or `.buildkite/`.

Transaction IDs and symbolic command names are restricted filename-safe tokens. JSON requests reject duplicate keys. Request size, patch size, snapshot size/file count, command-output capture, transport waits, and diagnostics output are bounded.

## Snapshot filtering

Snapshots contain only tracked files and omit `.git` history, untracked contents, binary files, symlinks, oversized files, common secret/configuration paths, private-key formats, Terraform state, and high-confidence private-key/service-account content even when it is stored under an innocuous filename. Sensitive omissions do not publish the sensitive relative pathname. Filtering is defence in depth, not a substitute for repository hygiene; no filename/content heuristic can prove that an arbitrary text file contains no secret.

Rename/copy detection is disabled while validating a staged patch so both the source and destination paths are checked independently. A transaction therefore cannot rename a filtered credential file to a benign name and expose it in a later snapshot.

## Configured-command trust boundary

The daemon never accepts command argv from a remote request. `run` contains only locally configured symbolic names. Before and after each configured command the bridge verifies the staged tree, HEAD, local Git configuration, and branch/tag/remote refs. It also rejects tracked worktree modifications made outside the staged patch. The bridge command environment removes common credential/token variables and the launcher's authoritative-checkout `PYTHONPATH`; Python projects receive the isolated worktree's `src/` instead. Captured stdout/stderr are bounded and written only to the user-private local state directory; remote results receive only the symbolic name, return code, and duration. Commands run in their own process group and leftover descendants are terminated.

**This is not an OS sandbox.** A locally configured test/build command can execute code from the patched repository as the local user. Malicious patched code may therefore access user-readable files or the network while that command runs, regardless of the fixed command argv. Configure `run` commands only for repositories where executing the proposed code under your account is acceptable. Strong isolation requires an external VM/container/sandbox or a separate low-privilege account; the bridge does not claim to provide that boundary.

## Opt-in push policy

Push is disabled by default and enabled only by a local per-repository setting. Even then, a transaction must separately request `"push": true`. The bridge pushes only the validated safe-prefix branch to hard-coded `origin`, with no force option, with hooks disabled, and with interactive credential prompts suppressed. Credential material and raw push diagnostics are never returned remotely.

A branch push can trigger **existing** Git hosting CI/workflows, and those workflows may execute the patched code. Blocking modifications to workflow/configuration files prevents one direct escalation path but cannot make an already-configured CI system safe for untrusted code. Enable bridge push only when the repository's existing CI policy is appropriate for remotely proposed branches.

## Transport and local-state hardening

The persistent rclone RC API is bound only to an app-private Unix-domain socket inside a `0700` state directory; the socket is tightened to `0600`. RC access is equivalent to local shell access, so it is never exposed over TCP. Only one bridge watcher can hold the mailbox-consumer lock at a time. A new watcher retires any stale app-private rclone RC process before starting its own, ensuring daemon restarts reload current OAuth/backend configuration.

RC reads may fall back to bounded subprocess operations. Mutating RC operations do not immediately race an ambiguous RC timeout with a second writer, because Google Drive permits duplicate filenames. Result publication is locally durable first; after an ambiguous upload the recovery path checks for an existing remote result and observes a grace period before retrying. Remote acknowledgements are authenticated with a local HMAC key before they can rebuild replay markers, so a mailbox writer cannot suppress transaction execution merely by forging a result filename/object. The key is stored in the user-private configuration directory rather than disposable runtime state, is migrated from the legacy state location when present, and never leaves the local machine.

Bridge-created commits also carry reserved trailers binding the commit to both the transaction ID and a SHA-256 hash of the complete canonical request. If the daemon crashes after `git commit` but before its local/remote result is durable, a byte-for-byte equivalent retry can recover the already-created commit without rerunning the patch or validation commands. Reusing that transaction ID with a changed request is rejected. Reserved trailers cannot be supplied through `commit_message`.

Local config/state directories are user-only (`0700`). Command logs and atomic temporary files are created private. Remote error results are sanitized and do not include local paths, raw command output, rclone/Git stderr, OAuth credentials, or arbitrary exception text.

## Google Drive OAuth

`scripts/google_drive_oauth.py` migrates the configured bridge remote to a user-owned Google OAuth Desktop client using only Python's standard library and the already-installed rclone. It installs no Google software. The helper touches only the rclone remote named in the bridge configuration, preserves all non-OAuth remote settings (including `root_folder_id` and scope), stops the bridge watcher, and performs the unavoidable browser OAuth flow against a private temporary one-remote rclone config. Only after the new token can list the existing mailbox and complete a create/read-back/delete capability probe does it atomically install the new OAuth fields into the authoritative config. The raw client secret is sent only over stdin to `rclone obscure -`; it is never placed in argv and only the obscured form is persisted. Concurrent migrations are locked out, external config edits are detected before replacement, rollback is refused if another writer has changed the file after installation, temporary OAuth directories are private and cleaned, and the daemon is restarted only if it had been running. The downloaded OAuth JSON is removed after a successful migration unless explicitly retained.
