# Project state

## Decisions already made

- project name: `llm-git-bridge`;
- provider/model agnostic core and wire protocol;
- Google Drive/rclone is the first transport, not part of the core abstraction;
- Drive is a transient mailbox/cache, not a repository mirror or backup;
- many repositories are represented by a tiny remote index and materialised on demand;
- `.git` history is not uploaded by default;
- remote transactions may request only symbolic locally configured commands;
- safe-prefixed branches and commits are allowed; push is locally opt-in and transaction-explicit; merge remains disabled;
- latency is a first-class product requirement.

## Proven proof of concept

The earlier prototype completed an end-to-end local repo -> Drive -> LLM -> Drive -> worktree -> patch -> commit -> result round trip. Its successful reverse transaction created commit `b6b9034f5153763b58bc0d68e04497b521136de0` on `ai/bridge-reverse-test` from base `76c5908a3b1663af344dd3cee7a9aef86af2eff1`.

The prototype scripts are preserved under `scripts/prototype/` and should not be treated as production architecture.

## v0.2 safe point

The bootstrap establishes:

- a real Python package and CLI;
- repository discovery and a path-free remote index;
- on-demand filtered JSON snapshots;
- protocol-v2 single-file transactions;
- isolated worktree patch/commit processing;
- local symbolic command allowlists;
- result publication with branch snapshots deferred by default and materialisable on demand;
- unit tests;
- an optional user LaunchAgent installed by the bootstrap script;
- a pushed bootstrap branch rather than direct modification of `main`.

Still planned after this safe point:

- delta snapshots rather than full refreshes;
- cache/LRU expiry;
- transport interface generalisation beyond rclone implementation details;
- richer observability and remote latency measurements;
- Homebrew packaging;
- migration tooling for older prototype configurations;
- optional draft-PR workflows after explicit user opt-in.

## Bootstrap recovery / adversarial audit (2026-09-12)

The first v0.2 bootstrap failed on macOS because one registry-stability test attempted to create sibling directories named `Foo` and `foo`. The test itself was invalid on the default case-insensitive macOS filesystem; it has been replaced with distinct names that deliberately slugify to the same repository ID.

The recovery audit also hardened the implementation before first self-hosting:

- remote materialisation requests are now supported, so an LLM can activate a registered repository without asking the user to run a command;
- idle polling requires only one Drive directory listing; remote result listing occurs only for newly observed requests;
- rclone listing failures are surfaced rather than silently interpreted as an empty mailbox;
- per-materialisation full-root rescans were removed;
- snapshot total size/file budgets were added;
- branch snapshot directory tokens are collision-resistant;
- patches are applied with `git apply --index`, so only patch changes are staged and test-generated files cannot be swept into commits;
- Git hooks and commit signing are disabled for bridge-created commits;
- changes to protected/sensitive paths and non-regular file modes are rejected;
- the macOS LaunchAgent receives a PATH that includes the Homebrew rclone location;
- durable local publication markers preserve idempotency while reducing Drive round trips;
- runtime registry and snapshot uploads no longer perform a redundant `rclone mkdir` before `copyto`; setup-only mkdir calls use a short timeout;
- branch snapshots are no longer a mandatory synchronous edit step; safe-prefixed branches can be materialised on demand;
- remote-result reconciliation moved to watcher startup, removing a Drive result-directory listing from the normal first-seen transaction path while preserving restart replay safety;
- transport-stage timings are emitted in results and local JSONL metrics.

Self-hosting and opt-in safe pushing are now proven. The bridge has created, tested, committed, and pushed an `ai/*` branch to `origin` without terminal intervention.
