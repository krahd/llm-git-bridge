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

## Persistent rclone RC acceptance (2026-09-12)

After activation, a no-test push transaction used persistent `rcd` for transaction listing and download: listing fell to 0.343 s, request download to 0.611 s, and result upload to 1.749 s. The complete daemon poll was 6.980 s including 4.089 s of local Git work and a 2.385 s GitHub push. A diagnostics-only request completed its daemon poll in 4.000 s. This validates the persistent transport as a material latency improvement over per-operation rclone subprocesses.

A later explicit full materialisation exposed one transient RC-list failure: the automatic subprocess fallback succeeded but consumed 13.341 s before the request ran; the following poll was back on `rcd`. The next resilience step therefore bounds fallback list latency and restarts a dead RC process without changing mailbox semantics.

## Repeated adversarial hardening audit (2026-09-12)

A second adversarial pass treated the mailbox writer and proposed patch as hostile while preserving the explicit local-command trust boundary. The audit found and fixed several classes of defects that the earlier functional suite did not exercise:

- transaction IDs can no longer contain dot/path-like components, and duplicate JSON keys are rejected;
- request downloads, result uploads, metrics, and command-output capture are bounded;
- sensitive rename/copy sources are validated independently, symlink/submodule deletion is rejected, high-confidence private-key/service-account content is filtered, and sensitive omission paths are not disclosed;
- `.gitmodules` and common CI/automation control paths are protected;
- configured commands now import/test the isolated worktree rather than the authoritative checkout, cannot silently change the staged tree/Git control state, receive a scrubbed ambient environment, produce local-only bounded logs, and have leftover process-group descendants terminated;
- default transactions no longer build snapshots at all; explicit post-commit snapshot failures and push timeouts are secondary rather than misreporting a durable local commit as failed;
- only one watcher can consume the mailbox at a time, and stale persistent-rclone processes are replaced on watcher startup;
- RC download fallbacks use separate local temporary paths, while ambiguous mutating RC timeouts are never raced with an immediate second writer; recovery checks for an already-published remote result before retrying;
- user config/state directories are tightened to user-only permissions and transient per-request local patch/inbox state is removed after acknowledgement;
- the Google Drive OAuth migration helper installs no Google software, authenticates through a private temporary one-remote rclone config, preserves unrelated config bytes/non-OAuth settings, serializes concurrent migrations, rolls back on failure/cancellation, and validates the existing mailbox before installing the new token;
- remote result acknowledgements are HMAC-authenticated with a local-only key before startup reconciliation can rebuild replay markers, preventing a mailbox writer from forging a result to suppress execution.

The audit also makes the residual trust boundary explicit: a locally configured test/build command can execute code from the remotely patched worktree as the local user. Git-state verification, environment scrubbing, output/process bounds, and fixed argv reduce accidental/escalation paths but are not a substitute for OS-level sandboxing. Repositories requiring hostile-code execution isolation must run those commands in an external VM/container/low-privilege environment.

### Final crash/replay and OAuth hardening

The final audit cycle closed two additional failure classes and finished with 122 unit tests passing under repeated hash seeds, plus independent adversarial fuzz checks for transaction IDs, refs, authenticated results, strict JSON, and rclone-config preservation. Bridge commits now bind the transaction ID and canonical request hash in reserved commit trailers, allowing a daemon restart to recover a commit created immediately before a crash without rerunning remotely supplied code; changed payloads cannot reuse that transaction ID. Result-authentication key material is durable user-private configuration rather than disposable runtime state, with migration from the legacy state location.

The OAuth migration helper now obscures the client secret through rclone over stdin, verifies candidate credentials with an actual mailbox create/read-back/delete probe before changing the authoritative config, detects concurrent rclone-config edits, and performs only guarded rollback so another writer's changes are never silently overwritten. Strict JSON parsing rejects duplicate keys and non-standard non-finite constants.
