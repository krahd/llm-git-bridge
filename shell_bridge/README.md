# ChatGPT Shell Bridge v5

A privileged local-user shell tunnel carried through a Google Drive mailbox. v5 keeps raw shell transport simple while adding concurrent supervised requests, exact Drive-root identity, bounded rclone calls, diagnostics/health, and a durable Git workspace coordinator for safe multi-conversation repository work.

## Components

- `bridge.py`: mailbox daemon and raw shell executor.
- `workspace.py`: durable branch/worktree/checkpoint/integration coordinator used for ordinary repository mutations.
- `install.sh`: macOS installer/updater; discovers and pins the exact Drive root folder ID.
- `build_package.py`: deterministic-source package builder that excludes Python caches.

## Safety model

The raw shell runs as the logged-in user and is not a sandbox. Ordinary repository mutations should use `workspace.py`; canonical branches are integration targets, not conversational editing surfaces. Each workspace job owns a private branch and linked worktree. Successful mutating commands checkpoint and push that branch. Integration is fail-closed on same-resource or changed-path sibling work, remote target movement, failed validation, or failed remote verification.

See `../docs/shell-bridge-v5-architecture.md` and `../docs/shell-bridge-v5-adversarial-audit.md`.
