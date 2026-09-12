# Security model

The bridge deliberately exposes less capability than a shell or GitHub token.

## Allowed by default

- read selected text working-tree files through snapshots;
- create/edit/delete repository files through a validated patch;
- create/update branches under the safe prefix;
- run symbolic locally configured commands;
- create commits.

## Not allowed by the daemon

- arbitrary shell command execution received from the client;
- push, merge, force-push, repository administration, or credential access;
- branch names outside the configured safe prefix;
- patching when tracked local modifications are present;
- applying a transaction whose base SHA is stale.

## Snapshot filtering

Snapshots omit common secrets (`.env`, private-key/certificate formats, credentials/secrets files), dependency/build caches, binary data, symlinks, and oversized files. Filtering is defence in depth, not a substitute for repository hygiene. Future versions should support explicit per-repository include/exclude policy.

## Self-modifying policy

Remote transactions cannot directly alter the local command allowlist because the allowlist lives under the user's local bridge configuration, outside the repository. This prevents a patch from adding a malicious command and then immediately invoking it.
