# Using llm-git-bridge

## Repository discovery and roots

A repository root explicitly approves a local directory as an ongoing discovery and policy boundary. A host may configure zero, one, or many unrelated roots. While the watcher runs, it automatically discovers Git repositories created, cloned, removed, or moved beneath them and republishes the path-free remote index when effective membership or capabilities change. The default discovery interval is 30 seconds.

The interactive entry point is:

```bash
llm-git-bridge setup
```

Advanced/non-interactive root management is available separately:

```bash
llm-git-bridge roots list
llm-git-bridge roots add ~/repos --push enable
# Optional: choose another bounded interval (5–3600 seconds).
llm-git-bridge configure-discovery --interval 30
# Optional: force an immediate full Git/state refresh.
llm-git-bridge roots scan
```

New repositories inherit the policy of their most-specific containing root. A nested root with the same policy is redundant and is compacted; a nested root with a different policy is retained as an intentional exception. You do not register newly created repositories by hand. Automatic discovery never authorises directories outside configured roots. The authoritative Git repositories remain local.

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

Push permission normally comes from the repository's most-specific configured root. For example:

```bash
llm-git-bridge roots add ~/repos --push enable
```

That permission applies to repositories already present beneath the root and to repositories discovered there later. A repository can be an exception:

```bash
llm-git-bridge configure-push my-repo disable
llm-git-bridge configure-push my-repo inherit
```

`inherit` removes the exception. Root permission is still only the local half of the gate: a transaction must also contain `"push": true`. The bridge pushes only the validated safe-prefix branch to `origin`, without force.

The public `repos.json` advertises effective `read`, `edit`, and `push` booleans so a remote client can avoid requesting unavailable capabilities. These booleans do not widen authority: protected/default-branch promotion remains intentionally outside the remote protocol. Perform promotion through your normal Git/GitHub review policy after validating the safe branch.

## Doctor and diagnostics

`doctor` reports operational state such as bridge/Python/Git/rclone versions, rclone-RC health, and whether a custom Drive OAuth client ID is configured.

`diagnostics` returns a bounded view of recent daemon metrics without exposing raw local logs or filesystem paths.

Both are protocol-v2 request kinds; see [protocol.md](protocol.md).

`diagnostics` also reports path-free repository-discovery health: whether auto-discovery is enabled, its configured interval, last scan time/duration, membership changes, and current repository count. It never exposes approved-root or repository filesystem paths.

## Multiple clients

Several ChatGPT sessions or other clients can submit work to the same mailbox. One local watcher owns mailbox transport and result publication. When `max_workers > 1`, local edit execution may overlap across different canonical repositories; same-repository mutation remains serialised. There is no FIFO guarantee.

See [concurrency.md](concurrency.md) before using multiple editing sessions, especially for dependent work or work against the same repository.
