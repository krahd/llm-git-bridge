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


### Post-promotion acceptance and midpoint audit

After the adversarial-hardening branch was fast-forwarded onto the bootstrap branch, live acceptance materialised `bootstrap/v0.2-20260912-000856` at `f056de7493ee50e244326715287b4f9a5a7a7ca2`. A live doctor request then exposed a transport/application boundary defect: an rclone transaction-download timeout was being converted into a durable signed error acknowledgement even though the daemon had never obtained the request bytes.

The midpoint audit rechecked transaction/replay identity, result authentication, safe refs, patch and worktree boundaries, sensitive-path handling, configured-command isolation, push policy, Drive write ambiguity, OAuth migration rollback/concurrency, daemon locking, packaging, and bounded observability. The transport defect was the concrete code fault found. Transaction downloads now classify pre-content transport failures as transient: no result or replay marker is created, the inbox object remains untouched, and a later poll retries it. Deterministic content/protocol rejection remains terminal. Regression tests cover both behaviours.


### End-stage audit findings

The end-stage adversarial pass found two additional bounded-resource/diagnostic issues. First, `run` validated command names but did not bound the number of entries, allowing one valid request to amplify into an arbitrarily long sequence of locally configured command executions. Transactions now accept at most 16 command executions. Second, command stdout/stderr was correctly kept off the remote result but its local log directory was deleted immediately after acknowledgement, contradicting the documented diagnostic model and making failures unnecessarily opaque. Recent command logs are now retained locally with a ten-transaction retention bound; disposable command workspaces/homes are still removed immediately.

### Final acceptance gate repair

Live post-promotion self-testing exposed an integrity defect in the configured-command workspace filter. The content-level secret detector treated any occurrence of a private-key marker, or the field-name substrings used by a Google service-account credential, as a secret. The bridge's own `core.py` and `test_core.py` contain those literals as detector implementation and test fixtures, so they were silently omitted from the Git-metadata-free command workspace. This made the live test gate incomplete and caused `test_app.py` imports to fail. Secret-content recognition now requires an actual PEM block or a parsed service-account JSON object; source/test files that merely discuss or test those structures remain available to configured commands. Regression coverage checks both the positive secret cases and the false-positive source-code case.

### Legacy-filter bootstrap compatibility and final release gate

A second live isolation pass established that the repaired detector could not initially validate itself because the currently running pre-fix daemon still used the old literal-substring secret filter while constructing the candidate's configured-command workspace. In other words, the candidate's improved detector was never reached: the old daemon removed `core.py`/`test_core.py` before launching the candidate tests. This was a bootstrap-compatibility failure, not a Python 3.14 or application-runtime defect.

The candidate now avoids embedding the legacy private-key/service-account marker sequences literally in bridge source/test files while preserving the same positive secret-detection semantics at runtime. A regression test asserts both that real PEM/service-account credentials are detected and that the bridge's own source fixtures do not trigger the legacy filter. The complete repaired candidate passes 129 unit tests, including repeated runs under distinct `PYTHONHASHSEED` values, plus `git diff --check` and Python compilation.

The remaining release gate is deliberately operational rather than local: submit these exact bytes through the currently running bridge, require its configured `test` command to pass on macOS, require an explicit opt-in push of the resulting safe `ai/*` branch, then materialise that branch and verify its commit SHA/content independently. Only after those gates pass should the bootstrap branch be fast-forwarded again and the daemon restarted on the new commit.

### 2026-09-13 strict audit and latency pass

A fresh audit started from the live `bootstrap/v0.2-20260912-000856` snapshot at `3c80b233633377cdfb9ebba3d8b543ef08a40f74`, with the pre-change suite passing 129 tests. The correctness pass found configuration validation gaps: malformed local configuration could survive until a later runtime crash, command examples still showed the obsolete flat command map, and an explicit `watch --interval` override was not subject to the same finite/range checks as persisted configuration. Configuration is now structurally validated on load and before every write; polling intervals are constrained to 0.5–3600 seconds; command maps are repository-scoped in the example; and branch-prefix validation is performed without spawning Git on the watcher's once-per-poll config reload.

The performance pass removed three avoidable hot-path costs without weakening transaction checks. Repository state now uses one porcelain-v2 Git query rather than multiple subprocesses. Configured-command isolation copies only index-tracked regular files (still applying sensitive-path/content filtering) rather than recursively copying the entire worktree, so large untracked dependency/build trees no longer dominate command startup or become visible to patched code. Disposable-worktree cleanup no longer executes a repository-wide `git worktree prune` on every normal add/remove; prune is retained as a crash-recovery retry. Startup reconciliation now avoids listing the historical result archive when there are no pending requests or every pending request already has a local publication marker.

On a synthetic repository with 1,200 tracked files and 10,000 untracked files, the same local benchmark moved repository-state collection from 20.76 ms mean to 3.82 ms mean (about 5.4x faster), and command-workspace preparation from 0.877 s median to 0.183 s median (about 4.8x faster). The post-change suite contains 139 tests and passes under multiple `PYTHONHASHSEED` values; Python compilation, shell syntax, `git diff --check`, and a 10,000-case safe-prefix/Git-ref differential fuzz check are clean.

### 2026-09-13 live macOS performance root-cause pass

The strict-audit candidate completed successfully on the production Mac but exposed a separate host-sensitive performance problem: its configured `test` command took 373.1983 seconds, compared with roughly nine seconds for the same suite in the isolated audit environment. Live controlled probes measured 100 `git --version` executions at 7.1722 seconds and 100 trivial Python processes at 9.9538 seconds, establishing that process creation itself is materially expensive on this host. Instrumenting the pre-remediation 139-test suite counted 1,319 Git subprocesses, so repeated cheap Git calls were being amplified into minutes of wall-clock latency.

The remediation pass keeps the full test gate and transaction invariants. Test repositories now come from independent local clones of immutable class-level seed repositories instead of repeated init/config/add/commit sequences; AppTests no longer rediscover a repository whose registry fixture is already known. Runtime Git calls are reduced by implementing the documented ref-name rules inside the bridge's restrictive branch grammar, batching changed-path mode queries, combining bridge-commit metadata reads, reading tracked-clean state in one porcelain-v2 query, combining HEAD/symbolic-HEAD reads, reusing known command-control state, and skipping command-control capture when no command is requested. Disposable worktree creation disables `core.fsmonitor` only for that invocation, avoiding a documented contemporary Apple-Git/macOS worktree hang class without changing the user's global Git configuration. Python 3.12+ test runs report the 20 slowest tests.

After these changes, the local full suite has grown from 139 to 141 tests while falling to 7.836 seconds; Git subprocess count fell from 1,319 to 773 (41.4% fewer). A trivial one-file no-command transaction fell from 26 Git subprocesses before this pass to 18. A 5,000-case differential fuzz run found zero disagreements between the bridge's accepted safe-branch grammar and `git check-ref-format` for the tested input space. The remaining acceptance gate is the production Mac itself: apply these bytes on top of `9491b7340acbf1271aae6b61d506b1d2abc13aa6`, run the complete configured test command, compare directly with the 373.1983-second baseline, and only then promote the branch.

### 2026-09-13 second performance pass

The first live remediation commit `d76f0cb6c6d3dec5f1a7190c85edebdd3c911a47` passed the full configured test gate on the production Mac in 106.26 seconds, down from 373.1983 seconds for the strict-audit baseline. That confirms the principal diagnosis: repeated process launches, especially Git, dominate this host. The first pass reduced the instrumented suite from 1,319 Git processes to about 773 without skipping tests.

A second source-and-documentation audit removed further redundant Git invocations while retaining the same security and replay invariants. The repository-clean status query now also yields the current HEAD OID; exact branch lookup obtains the branch tip with `show-ref --verify --hash`; staged changed-path and old/new file-mode validation now uses one `git diff --cached --raw -z --no-renames --no-abbrev`; the raw diff also proves whether a patch produced any change; bridge commit OID, parents, and reserved trailers are read with one `git show`; and disposable worktrees rely on the already-documented `worktree remove --force` rather than a preceding `git clean`. The redundant `git apply --check --index` dry run was removed because Git documents normal apply as atomic on hunk failure; a new regression verifies a failed patch leaves neither a durable branch nor a changed main worktree.

After the second pass the suite contains 143 tests and passes locally. Instrumentation counts 591 Git subprocess launches for the full suite, a 55.2% reduction from the 1,319 baseline and a further 23.5% reduction from the first pass. A normal one-file transaction with no configured command now uses nine Git processes, half of the first-pass 18-process measurement and 65.4% below the earlier 26-process baseline. The production-Mac acceptance result remains to be measured for these exact bytes before promotion.

The transport audit also found an operational requirement rather than a code hot-path defect: the live `doctor` reports `custom_drive_client_id_configured=false`. Current rclone Google Drive documentation states that rclone's shared Drive client ID is being retired during 2026 and recommends a user-owned client ID; the existing `scripts/google_drive_oauth.py` migration remains the supported path and must be completed interactively because Google requires browser-side project/OAuth consent actions. No global Git configuration or mailbox semantics are changed by this pass.

## v0.3 single-thread gold baseline (2026-09-13)

Phase A hardened, fuzzed, fault-injected, and re-profiled the serial execution model before any daemon concurrency was introduced. `0.3.0` freezes that behaviour as the semantic oracle for the scheduler refactor. The verified A3 precursor is `ai/single-thread-a3-20260913` at `33ffcdd091fcae1be80915d4f8a18389ff31be0e`, with 193 tests and an independent 33/33 tracked-file hash match.

The gold line retains one watcher and one synchronous local execution stream. Test validation may use the repository's bounded sharded test runner; this does not change daemon execution semantics. Concurrency work must first reproduce this behaviour with a one-worker scheduler before enabling simultaneous work across independent repositories.

The gold qualification corpus records repeated production-Mac test runs and live request/materialisation/restart samples outside the repository so measured host variance is preserved without rewriting code to chase a single best-case number.
