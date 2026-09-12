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

Full snapshots are uploaded on explicit materialisation. Edit transactions no longer synchronously publish a branch snapshot by default; a client can materialise a safe-prefixed branch on demand, or request `publish_snapshot: true` when it specifically needs the snapshot as part of that transaction. Incremental deltas and active-cache eviction remain planned optimisations.

## Transaction model

Transactions are single JSON objects containing an inline patch. The daemon checks the repository/base SHA, creates or reuses a safe-prefixed branch, applies and stages only the patch in an isolated worktree, runs only locally configured symbolic commands, commits, optionally pushes, uploads the result, and removes the temporary worktree. Branch snapshots are outside the synchronous hot path unless explicitly requested.

Bridge commits suppress Git hooks and local commit signing. Patch-created symlinks/submodules and changes to protected/sensitive paths are rejected. Test/build commands may create temporary files, but those files are not added to the commit. When a branch snapshot is requested or later materialised, it describes only committed tracked content.

## Performance principle

Drive round trips dominate latency. The watcher performs one transaction-directory listing per idle poll. Remote result reconciliation now happens once at watcher startup, rebuilding persistent local publication markers before any transaction is processed; newly observed transactions therefore do not pay a second result-directory listing. Materialising one repository refreshes only that repository rather than rescanning every configured root.

Runtime registry and snapshot writes go directly through `rclone copyto` rather than issuing a separate `rclone mkdir` first. The mailbox's fixed top-level directories are created during setup, and explicit mkdir operations use the same short timeout policy as polling. Branch snapshot publication is also deferred by default, removing a non-critical Drive upload from the edit acknowledgement path.

Each result reports transaction-list, transaction-download, and pre-result-upload timings. The daemon also appends local JSONL metrics including result-upload duration, and the protocol exposes a bounded diagnostics request that returns only a strict whitelist of timing/count fields. This allows transport latency to be separated from Git/test time without exposing local paths. The target bridge overhead remains approximately 2–5 seconds for normal interactions, excluding model work and local tests. Incremental repository deltas are the next major latency/bandwidth optimisation after self-hosted v0.2 is stable.
