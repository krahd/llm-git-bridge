# Architecture

`llm-git-bridge` has three layers.

1. **Core Git bridge**: repository discovery, state tracking, snapshots, transaction validation, worktrees, patch application, configured checks, commits, results, and safety policy.
2. **Transport adapters**: Google Drive through `rclone` first. The transport is a mailbox, not the source of truth.
3. **LLM/client adapters or usage guides**: ChatGPT is the first demonstrated client, but the wire protocol does not encode provider-specific assumptions.

## Execution and concurrency model

The mailbox may have multiple remote writers, but exactly one local watcher consumes it and owns all Drive/rclone side effects. The release-candidate scheduler can execute local transactions concurrently across different canonical repository paths when `max_workers > 1`; same-repository mutation remains serialised and the migration default is still `max_workers=1`. A bounded watcher-owned backlog and round-robin repository admission prevent avoidable head-of-line blocking without promising global FIFO. See [concurrency.md](concurrency.md) for multi-client guidance.

The post-v0.3 scheduler refactor preserves these semantics behind explicit poll,
classification, recovery, download/validation, execution, durable-result, and
publication/cleanup stages before any worker count is raised. See
[scheduler-architecture.md](scheduler-architecture.md) for the staged contracts
and worker-lifecycle rationale.

Version `1.0.0rc2` retains the Phase B ownership boundary while allowing a bounded number of repository-local workers. Drive/rclone transport and durable result publication remain watcher-owned; the scheduler owns worker lifecycle, canonical-repository exclusion, bounded backlog admission and fairness. The complete ownership and recovery contract is documented in `scheduler-architecture.md`.

## Multi-repository model

Do not mirror every repository to Drive. The local daemon recursively discovers repositories under configured roots and publishes a small path-free repository index. Repository contents are materialised only on demand. A remote client can request materialisation through the same request mailbox, so changing projects does not require a terminal command.

The authoritative repositories always remain local.

## Snapshot model

A snapshot contains tracked text working-tree content, branch/HEAD metadata, hashes, and an omitted-file manifest. Untracked presence is reported in repository state but untracked contents are not uploaded in v0.2, avoiding a mismatch with commit-based worktrees. It does not contain `.git` history. Common dependency/build directories, sensitive filenames, symlinks, binary files, and oversized files are omitted by default.

Snapshots also have a total byte/file budget. High-value project metadata and source/test/documentation trees are prioritised before less likely content. This prevents a repository containing thousands of individually small files from producing an unbounded mailbox object.

Full snapshots are uploaded on explicit materialisation. Edit transactions no longer synchronously publish a branch snapshot by default; a client can materialise a safe-prefixed branch on demand, or request `publish_snapshot: true` when it specifically needs the snapshot as part of that transaction. Incremental deltas and active-cache eviction remain planned optimisations.

## Transaction model

Transactions are single JSON objects containing an inline patch. The daemon checks the repository/base SHA, creates or reuses a safe-prefixed branch, applies and stages only the patch in an isolated worktree, runs only locally configured symbolic commands, commits, optionally pushes, uploads the result, and removes the temporary worktree. Branch snapshots are outside the synchronous hot path unless explicitly requested.

Bridge commits suppress Git hooks and local commit signing. Symlink/submodule creation, modification, and deletion are rejected, as are protected/sensitive paths, `.gitmodules`, and common CI/automation control files. Rename/copy validation checks source and destination independently. Test/build commands may create temporary files, but those files are not added to the commit. Command execution verifies that the staged tree and Git control state did not change, uses the worktree's Python import path rather than the authoritative checkout, bounds local output capture, scrubs common credential environment variables, and terminates leftover process-group descendants. These are integrity/resource controls, not an OS sandbox: configured commands may execute patched code with the user's filesystem/network privileges.

When a branch snapshot is requested or later materialised, it describes only committed tracked content. Default edit transactions do not even construct a snapshot synchronously; snapshot construction and publication are both deferred unless `publish_snapshot: true` is explicitly requested.

## Performance principle

Latency has two distinct regimes. Drive round trips dominate idle mailbox/materialisation work, while configured-command transactions can be dominated by local process creation on macOS. Live profiling on the production host measured roughly 71.7 ms per trivial Git process and 99.5 ms per trivial Python process; the pre-optimisation 139-test suite launched 1,319 Git processes. The bridge therefore treats subprocess count as a first-class latency budget without removing validation. Branch-ref checks use the documented Git ref rules inside the bridge's stricter allow-list, exact branch lookup obtains existence and tip OID in one `show-ref --hash` query, staged path and file-mode validation uses one NUL-safe raw diff, commit identity/parents/OID are read in one query, known control-state values are reused, and test fixtures clone an immutable local seed rather than rebuilding repositories from several Git commands each time. A failed `git apply --index` is atomic by Git's documented default, so the bridge does not precede the real apply with a redundant dry-run apply; it retains the separate staged `diff --check` and all sensitive-path/mode validation. Disposable worktrees are removed with `worktree remove --force`, which Git documents as sufficient for unclean worktrees, so no separate `git clean` process is required before disposal. Python 3.12+ test runs also report the slowest tests with `unittest --durations`.

Drive round trips dominate the transport side of latency. The watcher performs one transaction-directory listing per idle poll. Processed requests are best-effort removed from that inbox after their durable result is published, with one older acknowledged request reaped during idle polls; this prevents polling cost from growing with the lifetime transaction count. Remote result reconciliation now happens once at watcher startup. It first checks whether any inbox request lacks a durable local publication marker; if none do, it skips the historical result-directory listing entirely. Otherwise it intersects still-present transaction/result names and verifies the result HMAC before rebuilding a missing local publication marker. Newly observed transactions therefore do not pay a result-directory check on the steady-state hot path. Materialising one repository refreshes only that repository rather than rescanning every configured root. Repository state is collected with one `git status --porcelain=v2 --branch` process instead of separate HEAD/branch/dirty/untracked probes. Configured-command workspaces are populated from the Git index only, so unrelated untracked dependency/build trees are neither traversed nor exposed to patched code. Disposable worktrees are pruned globally only when an add/remove operation indicates stale crash metadata, rather than on every transaction.

Runtime registry and snapshot writes go directly through `rclone copyto` rather than issuing a separate `rclone mkdir` first. The mailbox's fixed top-level directories are created during setup, and explicit mkdir operations use the same short timeout policy as polling. Branch snapshot publication is also deferred by default, removing a non-critical Drive upload from the edit acknowledgement path.

The watcher now attempts to keep one `rclone rcd` process alive and routes list/copy/mkdir operations through its RC API over a Unix-domain socket. A singleton watcher lock prevents two local consumers from executing the same mailbox request concurrently, and startup replaces any stale app-private RC process so daemon restarts reload current rclone/OAuth configuration. This keeps rclone's backend process and Drive filesystem state warm instead of constructing a new rclone process for every mailbox operation. The socket is stored inside the bridge's user-private state directory and tightened to user-only permissions; the RC API is never exposed on TCP. Because rclone RC is equivalent to local code execution for anyone who can reach it, the subprocess transport remains the automatic fallback rather than broadening socket access. The watcher health-checks the local RC process and restarts it if it dies; fallback transaction listings use a shorter timeout so one transient RC failure cannot turn into another long synchronous poll. Control-plane transaction downloads and result uploads use bounded RC/subprocess timeouts rather than inheriting the much longer bulk-transfer timeout used for snapshots. Read downloads use separate temporary destinations so a timed-out RC read cannot race a fallback writer on the same local file. Mutating RC calls never immediately fall back after an ambiguous timeout; Drive permits duplicate filenames, so the safer recovery is to retry only after checking durable remote state.

Each result reports transaction-list, transaction-download, and pre-result-upload timings plus whether list/download used persistent `rcd` or the subprocess fallback. The daemon also appends local JSONL metrics including result-upload duration and transport mode, and the protocol exposes a bounded diagnostics request that returns only a strict whitelist of timing/count fields. This allows transport latency to be separated from Git/test time without exposing local paths. The target bridge overhead remains approximately 2–5 seconds for normal interactions, excluding model work and local tests. Incremental repository deltas remain a later latency/bandwidth optimisation after the transport path is stable.
