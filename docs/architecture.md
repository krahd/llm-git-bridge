# Architecture

`llm-git-bridge` has three layers.

1. **Core Git bridge**: repository discovery, state tracking, snapshots, transaction validation, worktrees, patch application, configured checks, commits, results, and safety policy.
2. **Transport adapters**: Google Drive through `rclone` first. The transport is a mailbox, not the source of truth.
3. **LLM/client adapters or usage guides**: ChatGPT is the first demonstrated client, but the wire protocol does not encode provider-specific assumptions.

## Multi-repository model

Do not mirror every repository to Drive. The local daemon recursively discovers repositories under configured roots and publishes a small path-free repository index. Repository contents are materialised only on demand. A remote client can request materialisation through the same request mailbox, so changing projects does not require a terminal command.

The authoritative repositories always remain local.

## Snapshot model

A snapshot contains tracked text working-tree content, branch/HEAD metadata, hashes, and an omitted-file manifest. Untracked presence is reported in repository state but untracked contents are not uploaded in v0.2, avoiding a mismatch with commit-based worktrees. It does not contain `.git` history. Common dependency/build directories, sensitive filenames, symlinks, binary files, and oversized files are omitted by default.

Snapshots also have a total byte/file budget. High-value project metadata and source/test/documentation trees are prioritised before less likely content. This prevents a repository containing thousands of individually small files from producing an unbounded mailbox object.

The first v0.2 implementation uploads full snapshots on explicit materialisation and after a successful transaction. Incremental deltas and active-cache eviction remain planned optimisations.

## Transaction model

Transactions are single JSON objects containing an inline patch. The daemon checks the repository/base SHA, creates or reuses a safe-prefixed branch, applies and stages only the patch in an isolated worktree, runs only locally configured symbolic commands, commits, uploads a branch snapshot, uploads a result, and removes the temporary worktree.

Bridge commits suppress Git hooks and local commit signing. Patch-created symlinks/submodules and changes to protected/sensitive paths are rejected. Test/build commands may create temporary files, but those files are not added to the commit and are removed before the branch snapshot is published.

## Performance principle

Drive round trips dominate latency. The watcher therefore performs one transaction-directory listing per idle poll. It lists results only when an unknown request first appears, using persistent local publication markers thereafter. Materialising one repository refreshes only that repository rather than rescanning every configured root.

The target bridge overhead remains approximately 2–5 seconds for normal interactions, excluding model work and local tests. Incremental repository deltas are the next major latency/bandwidth optimisation after self-hosted v0.2 is stable.
