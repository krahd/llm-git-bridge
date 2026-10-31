# Locating repositories in an operator-owned deployment

This distributable skill does **not** contain personal repository inventories, absolute home paths, or live mailbox IDs. An agent must obtain such information from its operator's private deployment configuration and the actual Mac checkout.

1. Read `health.json` from the operator-configured mailbox and compare the pinned folder IDs with the actual selected Drive folders.
2. Identify the allowed root from **local** configuration, not from examples or a downloaded public skill.
3. Use a narrowly scoped, read-only request to inspect the target Git repository's actual `git status`, `git rev-parse --show-toplevel`, and remotes.
4. Do not assume that every child of an allowed root is a Git repository; do not recursively enumerate the user's entire home.
5. For substantial edits, use the installed trusted workspace coordinator and reconcile the current local tree against the intended GitHub remote.
6. Preserve dirty/untracked changes and detached worktrees. Do not reset, clean or overwrite merely to align with remote source.

For request format, exact receipt verification and failure handling, use the [v6 client guide](https://github.com/krahd/llm-git-bridge/blob/main/docs/v6-client-guide.md). The [Mac Git Bridge entrypoint](../SKILL.md) is the public client control plane.
