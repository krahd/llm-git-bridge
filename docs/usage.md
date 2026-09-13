# Using llm-git-bridge

## Repository discovery

`add-root` registers directories that may contain repositories. `scan` refreshes the local registry and publishes the path-free remote index.

```bash
bin/llm-git-bridge add-root ~/repos
bin/llm-git-bridge scan
bin/llm-git-bridge status
```

The authoritative Git repositories remain local.

## Materialising snapshots

A client can request an up-to-date filtered snapshot of a repository or a safe-prefix branch. Materialisation is explicit so the bridge does not continuously mirror every repository.

Local CLI:

```bash
bin/llm-git-bridge materialize my-repo
```

Remote protocol requests are documented in [protocol.md](protocol.md).

Snapshots contain tracked text working-tree content plus Git state and hashes. They omit untracked contents, `.git` history, sensitive paths/content, binary files, symlinks, and files outside configured budgets.

## Editing a repository

A remote edit transaction supplies:

- a globally unique transaction ID;
- a repository ID;
- the exact base commit SHA;
- a safe-prefix branch name;
- a unified diff;
- optional symbolic validation commands;
- a commit message;
- optional safe-branch push and snapshot publication flags.

Example:

```json
{
  "protocol": 2,
  "kind": "transaction",
  "transaction_id": "tx-docs-20260913-8c19",
  "repo": "my-repo",
  "base_sha": "0123456789abcdef0123456789abcdef01234567",
  "branch": "ai/docs-refresh",
  "patch": "diff --git ...",
  "run": ["test"],
  "commit_message": "Refresh documentation",
  "push": true,
  "publish_snapshot": false
}
```

The daemon processes the transaction in an isolated Git worktree. It validates the patch and staged tree, runs only locally configured symbolic commands, creates a commit, optionally pushes the safe branch, publishes a signed result, and then removes the temporary worktree.

## Branch semantics

The bridge deliberately does not rebase or merge remote requests for you.

For a new branch, the transaction `base_sha` must still equal the authoritative repository HEAD when the request is processed. For an existing bridge branch, the branch tip must match the expected base unless the daemon is recovering the exact same previously committed transaction after a crash.

This fail-closed model makes stale work visible instead of silently combining concurrent edits.

## Validation commands

Configure allowed symbolic commands locally:

```bash
bin/llm-git-bridge configure-command my-repo test \
  python3 -m unittest discover -s tests -v
```

A remote transaction can request `"run": ["test"]`; it cannot replace the configured argv.

The bridge records only command name, return code, and duration in the remote result. Bounded stdout/stderr logs stay in the local private state directory.

## Push policy

Enable pushing per repository:

```bash
bin/llm-git-bridge configure-push my-repo enable
```

A transaction must also contain `"push": true`. The bridge pushes only the validated safe-prefix branch to `origin`, without force.

Merging to a protected/default branch is intentionally outside the remote protocol. Perform promotion through your normal Git/GitHub review policy after validating the safe branch.

## Doctor and diagnostics

`doctor` reports operational state such as bridge/Python/Git/rclone versions, rclone-RC health, and whether a custom Drive OAuth client ID is configured.

`diagnostics` returns a bounded view of recent daemon metrics without exposing raw local logs or filesystem paths.

Both are protocol-v2 request kinds; see [protocol.md](protocol.md).

## Multiple clients

Several ChatGPT sessions or other clients can submit work to the same mailbox. Requests are consumed by one local watcher and therefore execute serially. There is no FIFO guarantee.

See [concurrency.md](concurrency.md) before using multiple editing sessions against the same repository.
