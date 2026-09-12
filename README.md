# llm-git-bridge

`llm-git-bridge` is a provider-agnostic bridge between LLM/agent clients and local Git repositories when a native GitHub integration is unavailable or administratively blocked.

The Git repositories remain local. A mailbox transport exposes a small repository index, on-demand snapshots, transactions, and results. Google Drive via `rclone` is the first transport; the core protocol is intentionally not tied to ChatGPT or Google Drive.

## Current state

The initial Drive proof of concept completed a full round trip: local repository -> Drive -> LLM -> Drive -> isolated Git worktree -> patch -> commit -> result. v0.2 turns that experiment into a reusable bridge with multi-repository discovery, a provider-neutral protocol, single-file transactions, safety checks, and a background watcher.

## Local development

```bash
bin/llm-git-bridge setup
bin/llm-git-bridge add-root ~/tom-repos
bin/llm-git-bridge scan
bin/llm-git-bridge materialize llm-git-bridge
bin/llm-git-bridge status
bin/llm-git-bridge watch --once
```

The bootstrap script can also install a user LaunchAgent so the watcher runs without a terminal.

## Protocol sketch

Drive/mailbox layout:

```text
v2/meta/repos.json
v2/repos/<repo-id>/snapshot.json
v2/repos/<repo-id>/branches/<branch>/snapshot.json
v2/transactions/<transaction-id>.json
v2/results/<transaction-id>.json
```

A transaction is one JSON object with an inline unified diff. This avoids the multi-file transaction latency of the prototype.

```json
{
  "protocol": 2,
  "kind": "transaction",
  "transaction_id": "tx-example",
  "repo": "llm-git-bridge",
  "base_sha": "0123456789abcdef0123456789abcdef01234567",
  "branch": "ai/example",
  "patch": "diff --git ...",
  "run": ["test"],
  "commit_message": "Example bridge change"
}
```

The daemon does not execute arbitrary shell commands from a remote transaction. `run` contains symbolic names whose argv are configured locally.

Push is a separate, local opt-in. Enable it per repository with `bin/llm-git-bridge configure-push <repo> enable`; a transaction must additionally request `"push": true`. The bridge then pushes only the validated safe-prefix branch to the hard-coded `origin` remote, with no force option and with Git hooks disabled.

## Safety defaults

- repository paths stay local and are not included in the remote registry;
- sensitive files and common credential formats are excluded from snapshots;
- `.git` history is not uploaded;
- tracked local modifications block remote patch application;
- remote branches must use the configured safe prefix (`ai/` by default);
- push is disabled by default and can only be enabled per repository by local configuration;
- when enabled and explicitly requested, only the transaction's validated safe-prefix branch is pushed to `origin`, without force;
- merge, force-push, remote mutation, and repository administration are not implemented;
- each transaction is processed in an isolated Git worktree;
- base SHA checks prevent silently applying stale patches.

## Documentation

See `docs/architecture.md`, `docs/protocol.md`, `docs/security.md`, and `docs/project-state.md`.
