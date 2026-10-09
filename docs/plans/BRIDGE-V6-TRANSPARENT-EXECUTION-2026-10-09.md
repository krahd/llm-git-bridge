# Local Executor Bridge v6: transparent execution audit and implementation plan

**Status:** implementation-ready proposal; **not** a production release authorization.  
**Prepared:** 2026-10-09 UTC. **Owner:** `krahd/llm-git-bridge`.  
**Baseline:** GitHub `main` observed and used by the durable workspace coordinator at `87a1e8bcfa736ab546b95b00cf81845958c301ed` (2026-10-09 06:02 UTC).  
**Input:** *GitHub ecosystem audit: Agent instructions, Work Web, bridges and continuity* (Astra, 2026-10-09, 14 pages; supplied file SHA-256 `bfd747b17b1a3fae692c06982ef78c180918090235814eaa6b2a4ae7d8e9f672`), plus read-only bridge/source/runtime inspection from an ordinary ChatGPT Edu conversation (2026-10-09 05:50-05:57 UTC).  
**Intended result:** one v6 production local executor, with proven ordinary-Edu operation, no routine approval fatigue, conservative preservation, durable jobs, and independently verified GitHub completion.

> **Reading rule:** This plan describes **desired future behavior** unless explicitly marked *observed* or *tested*. Do not infer that staging has passed an acceptance gate merely because a feature exists in source or the service is running. Preserve v5 production until a qualified v6 cutover and rollback test. Treat earlier cutover plans as historical inputs, not as contradictory current release instructions.

## 1. User constraints and success contract

This ChatGPT Edu environment lacks Work, Developer Mode and the GitHub connector. If those features were available, this bridge would be largely unnecessary. **Therefore v6 must work from an ordinary restricted Edu conversation through an interface actually exposed there.** Our available Google Drive/file-management transport has now been tested from this exact environment. A hypothetical custom MCP integration must not be a release prerequisite.

Success is measured by behaviors rather than number of components:

1. A conversation submits a bounded request and receives its exact result with a durable operation identity; a lost response never causes blind replay.
2. Authorized reads, edits, tests, and Git development work run without routine human confirmations; the executor denies requests beyond granted authority.
3. Existing uncommitted files, unpushed commits, branch tips, external masters, and recoverable results are not discarded for convenience.
4. A development task can be resumed, checkpointed, validated, integrated into the intended canonical GitHub branch, and independently verified, without requiring the user to administer its Git lifecycle.
5. Consequential external actions require informed review when policy demands it; review must show the exact effect and command, not just an agent-supplied explanation.
6. Runtime identity, capability, build version and last acceptance are truthful and discoverable before client submission.
7. The bridge remains **execution infrastructure**. Domain repositories own the substantive project files, `tom-work-admin` owns central identity/register facts, Work Web is a view/edit surface, and Buork owns higher-level WorkThreads and provider continuity. The bridge is **not** a competing project registry.

**Transparent** means low operational friction **and** inspectable effects. It does not mean invisible background mutation, suppression of consequential approvals, or pretending that the bridge can continue ChatGPT's reasoning after a conversation dies. Its processes and durable jobs may continue; a provider's cognitive work must be resumed separately.

## 2. Evidence and adversarial assessment (as of the baseline)

| ID / priority | Finding and evidence | Implication / limit | Required disposition |
| --- | --- | --- | --- |
| T0 / proven | A raw request sent through the ordinary Edu file-management-to-Drive path executed on v5 and returned an exact result. A separate read-only request to v6 staged candidate `87a1e8bc…` completed (exit 0; 0.043 s **Mac command time**, not end-to-end latency). | The earlier claim that this client cannot submit raw requests was false. Single-request success does not establish reliability or write authority. | Retain Drive transport as the qualified baseline; test fresh-chat read/write/recovery. |
| R0 / observed | `health.json` identified production v5 at 2026-10-09 05:52:49 UTC; a v6 staging LaunchAgent and manifest identify `87a1e8bc…` at the inspected time. | A running process/manifest is not a complete security or approval acceptance test. | Keep v5 protected; verify deployed binary/asset hashes before qualifying v6. |
| G1 / P0 **reproduced externally** | Astra's disposable-repo test: GC deleted a remote job branch after that branch gained a later, unintegrated commit; the new commit remained locally reachable in the test, but its remote recovery ref disappeared. Source: `shell_bridge/workspace.py` near `gc_jobs`, `87a1e8bc…`. | Not proof of permanent data loss on production; proof that current branch-deletion logic is unsafe. | **Disable remote deletion by this GC path immediately** until repaired and fault-tested. |
| G2 / P0 for unattended cleanup **reproduced externally** | GC attempted removal of a locked worktree, ignored errors, and deleted job metadata while the worktree and branch remained. | Recovery index can be erased while actual resources survive. | Check all removal outcomes; preserve terminal receipts; no false success. |
| I1 / P1 source reviewed | `integrate_job` runs validation only when provided by the caller; it accepts states beyond `ready`. There is no uniform mandatory validation for all publication routes. | Green local tests are not an enforced canonical admission policy. | Trusted validation contract, candidate-bound results, CI on `main`, and publisher parity. |
| I2 / P1 source reviewed | The coordinator has checkpoint and integration mechanisms, but does not offer a single durable, recoverable `finish` contract; a crash after push but before recording remains a reconciliation risk. | Repeating the integration may create duplicate semantic effects; a clean workspace alone is insufficient. | Receipt-aware, idempotent completion and same-job locking. |
| A1 / P0 source reviewed | The v6 menu review path may authorize a request without the exact command being displayed in the menu; `operator_confirmation_mode=off` remains capable of auto-allowing an otherwise required approval. | Convenience configuration can defeat human-required decisions. | Require explicit command/effect inspection for review; `off` means **reject elevated operations**, never allow. |
| A2 / P0 **guarantee gap** | Public-repository visibility and related controls rely partly on command-text regexes, while broad `system` shell can enable network and user-level permissions. | Regex coverage is not a non-bypassable security guarantee for arbitrary shell. | Enforce via capability/credential boundaries where possible; otherwise document limits and deny uncontainable operations. |
| D1 / P1 | README/architecture/cutover docs, versions of client instructions, cached `origin/main`, active checkout and installed runtime can disagree. Current checkout was dirty on `ai/installer-approval-integrity-20261003-a1` while the coordinator created this document job at `87a1e8bc…`. | Source, published remote, staging and production are four separate states. | Pin and expose verified runtime capabilities; one current status pointer, historical docs labelled. |
| E1 / dependency, **not bridge defect** | Astra reproduces Work Web form-snapshot/revision race and stale freshness caching; saves have incomplete durable retry identity. | A bridge-only patch cannot make Work Web consistent. | Track a bounded `tom-work-admin` follow-on; do **not** hold basic v6 shell cutover for all Work Web enhancements. |
| E2 / dependency | The six sampled core `main` branches were reported unprotected and bridge `main` lacked its qualification workflow at Astra's inspected commit; GitHub-side admission differs by publisher. | A bridge-local validator cannot protect direct GitHub/Work Web pushes. | Establish compatible branch checks and protections, subject to actual GitHub plan eligibility; distinguish bridge acceptance from full ecosystem enforcement. |

### What the Astra audit **does not** prove

- Counts of 97 accessible repositories and 556 branch refs are timestamped inventory, **not** proof of 556 unfinished jobs or current counts. Squash merges make ahead/behind misleading.
- Its cleanup reproductions used disposable Git repositories, not an induced deletion of production work.
- It did not qualify the installed Mac coordinator or staging approval UX; the independent read-only v6 smoke above extends evidence only for the exact tested path.
- Remote `main` and the local dirty branch are not interchangeable. The coordinator's 2026-10-09 workspace creation fetched/anchored the documented job at `87a1e8bc…`; future implementation must fetch anew.
- A successful `git push`, a health heartbeat, and a `finished` journal entry are not proof that substantive cross-repository work is complete or visible in Work Web.

## 3. Minimal architecture and responsibility boundaries

**Ordinary Edu client → file-management/Google Drive mailbox (transport only) → one local v6 daemon → trusted workspace coordinator for Git/network → authorized files and canonical GitHub.** Keep v6 staging separate until cutover. Do not add a second production daemon, new database, shadow registry, distributed orchestration engine, or MCP dependency.

### 3.1 Client protocol

A small client wrapper or skill must expose **submit, inspect, resume**, hiding the mechanics of JSON construction/upload and result lookup. It must use the pinned mailbox identity discovered in a verified runtime manifest, generate a stable unique request ID, and preserve the exact request hash, write scope, explanation, and operation IDs in durable records. Exact result lookup takes precedence over broad Drive searching. After ambiguous delivery, inspect the original ID; resubmit **only** when the daemon has authoritatively classified the prior request as never started, and use a new transport ID linked to the same logical operation where necessary.

Preflight verifies connector *read and write* capability in a fresh restricted Edu chat, mailbox identity, runtime version and supported scopes. If the file-management tool is unavailable in that context, fail with a concrete transport capability error rather than claiming Mac execution failed. The bridge cannot guarantee that all Edu conversations expose identical tools; this is an explicit acceptance test, not an assumption.

### 3.2 Runtime and privilege boundaries

Retain `read_only`, `repository`, `system`, `auto` as backward-compatible wire scopes, but keep the UI/skill simple: ordinary authorized work, elevated reviewed operation, or rejected. The client cannot grant itself capabilities by asking for `system`. Filesystem controls are operating-system-enforced and approved workspace roots must include legitimate worktree paths; validate symlinks and actual resolved roots. Raw repository shell stays network-denied by default; trusted Git coordinator operations use narrowly scoped credentials/network and known targets.

For arbitrary unrestricted `system` shell, non-publication and no-secrets guarantees **cannot** be proved with string matching. Human approval authorizes a specific payload/scope; it is not a magic security boundary. Prefer a mediated GitHub operation for public visibility changes with an explicit human-only path; if bypass remains possible with available credentials/network, state that limitation and withhold a claim of non-waivability.

On macOS, required elevation is a real, local decision: display exact `cwd`, command (inspectable without truncation), resource roots, network/capability scope, agent-provided intent clearly marked as *unverified*, and relevant effect warning. A signed/hashed approval binds to exact bytes, request ID, nonce and expiry; no default Allow, no silent bypass, denial/expiry fail closed. Menubar notifications are quiet until exceptional review; an uninspected summary must not expose an Allow action. `off`/unavailable UI **denies** required elevation; it does not disable safety.

### 3.3 Durable execution and completion

Do not confuse **request result** with **project completion**. Use the existing journal, workspace metadata, Git commit trailers and narrow durable receipts; avoid a new all-purpose state store. One operation can move through these independently evidenced states:

`submitted → accepted → started → finished` (shell lifecycle); and, only for Git work, `checkpoint_pushed → candidate_validated → integrated_remote → remote_verified → terminal_receipt`.

For cross-repository consequences, a separate small owner-directed completion record may additionally track `admin_reconciled` and `view_visible`; a bridge transaction must never invent semantic owners. Report a partial state honestly and return the unresolved obligations rather than collapsing every step into `success`.

The executor must preserve the original request ID and its output/reconciliation identity across provider-session loss. The bridge should not attempt to recreate lost ChatGPT reasoning; that is Buork's layer.

## 4. Implementation programme and stop/go gates

Each phase is a bounded change in a dedicated workspace based on **fresh verified GitHub `main`**. Checkpoints are pushed. Integration into canonical `main` is serialised and independently verified. No phase performs destructive cleanup to satisfy an aesthetic branch count.

### Phase 0 — freeze unsafe cleanup and reconcile state (P0; prerequisite)

**Change:** Add an explicit safe-disable for remote-branch deletion in the existing `gc` command; remove it from ordinary client paths. Check for any current executing GC before patching. Re-read canonical `main` and current job metadata with an isolated workspace; preserve the dirty `ai/installer-approval-integrity-20261003-a1` checkout and ignored `.worktrees-v6/` contents. Record build and runtime versions separately.

**Gate:** no supported client route can delete a remote job branch using unverified eligibility; no existing worktree, local-only change or ref is reset, cleaned or force-pushed. If the preflight finds an in-flight GC, reconcile its exact operation before another cleanup action.

**Recovery:** leave deletion disabled, preserve the job and its refs, report exact unresolved IDs. Do not migrate yet.

### Phase 1 — repair workspace preservation and integration foundations (P0/P1)

**Files:** `shell_bridge/workspace.py`, focused `shell_bridge/tests/test_workspace.py` plus one minimal integration test file only if needed.

**Change:** GC eligibility requires `integrated`/terminal state, same-job lock, inactive process, worktree cleanliness, recorded checkpoint, equality of both local and freshly fetched remote job tips with the expected preserved checkpoint, and evidence that its content reached the verified canonical integration. Protect exceptional historical artifacts with a named preservation/archive ref. Remove locked worktrees only after deliberate verified unlock; check each subprocess return code, verify absence, and retain a terminal receipt even on success. Remote deletion must be conditional on the expected remote ref (compare-and-delete / lease), and never run after a mismatch. Acquire the same per-job lock around `ready`, `integrate`, and `gc`; re-read state under lock. Do not make `git worktree remove --force` a default cleanup strategy.

**Adversarial tests:** new commit on integrated branch, concurrent remote branch advance between check and deletion, failed unlock/removal, dirty and untracked files, missed job metadata, crash between push and recording, stale target, and missing user-owned preservation refs. Require surviving remote ref + job record whenever a test cannot prove deletion safe. Use disposable bare remotes; do not simulate by deleting real remote branches.

**Gate:** both Astra reproductions now fail safely, no false GC success, no unique remote checkpoint can be erased under tested races. If it fails, leave GC deletion disabled while unrelated fixes proceed.

### Phase 2 — authorization, approvals, and sandbox usability (P0)

**Files:** `shell_bridge/bridge.py`, native `shell_bridge/approval_gui.swift`, approval helper, and their focused tests.

**Change:** remove `off` → allow semantics, fail closed when required approval cannot be collected, require human inspection of exact command/effect, preserve approval hash/nonce/expiry integrity, and demonstrate that unsafe subprocess/network paths cannot escape the claimed capability boundary. Fix allowlisted workspace roots that currently block normal worktree creation; test nested worktrees and symlink resolution. Keep routine authorized edits and builds approval-free.

**Adversarial tests:** disguised high-impact commands, shell wrappers, redirection/symlinks, script invocation, malformed request/cwd, stale or replayed approval, UI not running, denied/expired approval, and user-visible review that actually reveals command bytes. The tests must distinguish **detected** command patterns from **enforced** privilege boundaries. Where an effect cannot be bounded (e.g. unrestricted `system` network shell), require explicit elevation and document residual risk; do not claim impossible prevention.

**Gate:** zero approval popups for a defined routine test corpus, all required elevation either explicitly authorized for the bound payload or rejected, and successful actual UI smoke on the Mac desktop (not solely unit tests).

### Phase 3 — reliable `finish`, mandatory validation and GitHub admission (P1)

**Files:** coordinator + tests; minimum trusted per-repository validation configuration; main-branch CI.

**Change:** implement idempotent `finish(job_id)` as one **durable logical operation** composed of checkpoint, ready, validation, integration, independent remote verification, and terminal receipt. A validated candidate must be the exact tree pushed; validation requirements are derived from trusted, reviewed policy (not only optional request-supplied flags). An unknown or missing required validator blocks **publication**, preserving the candidate. Use the existing resource/path-overlap model; auto-reconcile only proven disjoint changes, revalidate after a changed target, and preserve both alternatives on semantic conflicts. After ambiguous push, inspect commit trailers/remote ancestry and validated content to identify already-completed integration, including squash/advanced-main cases; do not push a duplicate effect. Cleanup is optional and **never** part of the success gate.

Install bridge qualification checks on canonical `main`, not solely a historical feature branch. Add branch/ruleset protections only after verifying the GitHub plan's eligibility and adapting all existing publishers; a mandatory human PR review must not be imposed on routine agent work. A local `finish` alone cannot enforce validation for direct GitHub/Work Web edits: record that external-policy gap separately until equivalent admission checks exist.

**Gate:** fresh/overlapping writers, changed target, missing validator, stale-base, crash immediately after remote push, and `main` advancing immediately after success all lead to a correct terminal receipt or a durable blocked state. Canonical ref is independently observed and matching content is confirmed, not inferred from local HEAD.

### Phase 4 — restricted-Edu client reliability and diagnostics (P1)

**Change:** update the installed `mac-git-bridge` skill/client guidance to use the **verified** file-management raw-upload route, exact result retrieval, bounded request/recovery procedures and observed runtime manifests. Publish distinct `desired_source`, `installed_build`, `active_mailbox`, `protocol`, `capabilities`, `acceptance_revision/time` values. Classify failures by connector, transport, daemon, sandbox, approval, workspace, Git remote, and conversation delivery. Avoid broad mailbox polling and repeated rescans.

**Gate:** in at least two **new** ordinary Edu conversations, successful read-only and authorized-write operations; one lost-result recovery by ID; unambiguous rejection of unsupported capabilities; no hidden operator confirmation for normal authorized work. Report request round-trip duration separately from daemon command duration. The previously observed 0.043-second staged smoke is not an SLA.

### Phase 5 — qualify staging, cut over once, and retire verified legacy services (P1)

**Change:** stage the exact latest integrated commit with manifest hashes for daemon/coordinator/helper/native app/config/LaunchAgent. Validate from the real Mac GUI session, and run representative shell, workspace, approval, GitHub, timeout, restart and rollback scenarios. Verify v6 staging cannot consume v5 production mailbox. Require a signed or otherwise authenticated release receipt stating the exact passed candidate and acceptance results. Make cutover provisional until production acceptance and rollback viability are independently verified. Stop old consumers **only** as part of a reversible transaction; preserve backups and pending IDs. Retire inactive historical LaunchAgents only after identifying ownership and proving they are not used.

**Gate:** one and only one service consumes the production mailbox; active health and manifest agree on tested release identity, production smoke includes approvals and GitHub completion, v5 rollback was exercised, and no indeterminate mutation was silently replayed. On any failed gate, restore prior service and retain the candidate and operation evidence.

### Separate downstream track — Work Web and ecosystem consistency (not a v6 cutover blocker)

Astra identified real Work Web risks: cached freshness, old values paired with newer revision tokens, ephemeral failed-save candidates, inconsistent code/data generations, and central registry changes lacking owner evidence. The owner is `tom-work-admin`; the fix should use immutable data+revision snapshots, baseline-relative field changes, operation IDs, durable saves and narrow reconciliation. Do not turn these into new bridge-side semantic rules. Shared completion receipts can link owner actions and downstream consequences without introducing a shadow registry. Until repaired, Work Web must not advertise its forms as concurrency-safe merely because the bridge is v6. The separate Work Web plan should be owned, tested and integrated in its own repo.

## 5. Acceptance matrix (must be documented against exact build SHA)

| Case | Expected outcome | Release gate |
| --- | --- | --- |
| Fresh restricted Edu chat → Drive → staged daemon → result | Exact ID/status and output | Phase 4/5 |
| Normal authorized file edit / tests / Git checkpoint | No approval; correct sandbox bounds | Phase 2/5 |
| Required elevated effect, UI denied/unavailable/off | No STARTED child or effect; explicit rejection | Phase 2/5 |
| Required elevated effect, human reviews exact bytes | Payload-bound one-time decision; replay rejected | Phase 2/5 |
| Approved workspace created outside original repo but under configured workspace root | Succeeds without broad home access | Phase 2/5 |
| Long-running command timeout / daemon restart / chat timeout | Original status recovered, no blind mutation replay | Phase 4/5 |
| Locked worktree cleanup failure | Worktree/branch preserved; job+receipt remain discoverable | Phase 1 |
| Integrated branch advanced with new unique commit | Cleanup refuses deletion; remote recovery ref preserved | Phase 1 |
| Concurrent remote push during conditional deletion | Compare-and-delete fails safely | Phase 1 |
| Missing mandatory validator | Publication blocked; checkpoint retained | Phase 3 |
| Crash after successful GitHub integration, before local receipt | Resume detects existing canonical effect; no duplicate | Phase 3/5 |
| `main` advances after verified integration | Terminal receipt remains valid if intended content persists | Phase 3 |
| Staging → production → rollback | No competing consumers, no lost requests, v5 restored on failure | Phase 5 |

For reliability claims, record attempted N, pass/fail, latency distribution, environment and exact version. Do not promise 99% availability based on a handful of smoke tests. Tests are necessary but cannot prove absence of malicious shell escape or semantic project correctness.

## 6. Explicit exclusions and stop conditions

**Not in v6:** finished Buork, Work Web rewrite, cross-repository semantic auto-resolution, monorepo migration, universal schema migration, all 97 repos' policy conversion, bulk branch deletion, an additional cloud control plane, or a second always-on transport. Native MCP remains optional only if actually available in restricted Edu, not a precondition.

**Stop and preserve:** a branch/ref tip changes unexpectedly; a stored request may have executed but has no authoritative result; a validator is absent; sandbox or approval bypass is discovered; the Mac's active runtime does not match the claimed release; GitHub push or protection prerequisites are unavailable. Report the exact job/request, remote ref and last verified checkpoint, never erase recovery state to force completion.

**Safety tradeoff:** removing confirmation fatigue is not equivalent to unrestricted authority. A dedicated trusted GitHub client may make some public/credential-sensitive operations enforceable; unrestricted `system` shell with network and user credentials cannot offer the same guarantee. Maintain honest guarantees rather than ineffective regex promises.

## 7. Handoff to the next conversation / persistent executor

**Canonical source of this plan:** `krahd/llm-git-bridge/docs/plans/BRIDGE-V6-TRANSPARENT-EXECUTION-2026-10-09.md` on GitHub `main` *after verified integration*. Read it from GitHub fresh; do not trust a pasted transcript or an unverified Drive copy. Compare repo/source, local worktree, staged build and production runtime separately.

**Planning job:** `v6-transparent-plan-20261009-a1`, workspace resource `docs/v6-transparent-architecture`, repo `/Users/tom/tom-repos/projects/llm-git-bridge`, created from `87a1e8bcfa736ab546b95b00cf81845958c301ed`. A successor must first check whether this job is already integrated and verify the remote ref; **do not recreate or replay it** just because a chat ended. This ID belongs to the *documentation task*, not to future code changes.

**First implementation action once this plan is on verified `main`:** inspect current `shell_bridge/workspace.py` and the existing GC tests, create/resume a **new** isolated implementation job scoped to workspace preservation, prevent remote branch deletion through the unsafe path, and add the two reproduced-failure regression tests. Validate, checkpoint, integrate and verify remote. Then continue through the phases above.

**Other active work:** Preserve the dirty older local checkout `ai/installer-approval-integrity-20261003-a1` (observed modified `shell_bridge/approval_gui.swift`, `tests/test_shell_bridge_installer_portability.py`, and untracked `.worktrees-v6/`). Review before merging its still-unpublished changes; do not reset/clean/stash it. Production was observed as v5, v6 candidate `87a1e8bc…` staged; do not mistake either for a current fact without new inspection.

**Client route tested in this conversation:** `files__manage_library` upload of a local raw JSON into the pinned production or staging requests folder; Drive `list_folder` and `fetch` of exact result. This is an *observed available route*, not an instruction to assume availability in a new chat. Source filenames, IDs and command bytes for audit probes remain in transport results; GitHub is the authoritative persisted plan.

## 8. Fresh-eyes adversarial QA of the proposal

1. **Could automatic finish delete unfinished work?** No: cleanup is not a completion prerequisite; Phase 1 disables unsafe remote deletion until local/remote tip equality and lease/race tests succeed. Even a verified integration does not authorize removal of later commits.
2. **Does this plan promise exactly-once shell side effects?** No: it requires durable identity and reconciliation; arbitrary external effects cannot be atomically committed with the bridge journal. Indeterminate non-idempotent actions remain blocked for evidence-based resolution.
3. **Does a regex guarantee GitHub visibility safety?** No: treat pattern rules as supplementary signals and constrain privileged network/credential capabilities. No unsupported non-waivable guarantee is asserted.
4. **Could mandatory validation make work impossible?** Yes if policy is absent or too strict. Introduce reviewed per-repository minimum checks, verify CI eligibility, keep candidates recoverable when validators are missing; avoid blanket human PR review.
5. **Could a deployment manifest be stale or forged?** Yes if self-reported only. Compare on-disk hashes, launchctl process identity, immutable installation location, exact Git commit, and observed mailbox behavior during release qualification.
6. **Could a fresh Edu conversation lack the upload tool?** Yes. Qualify the client path in fresh conversations and classify missing tools as a platform capability limitation; do not misdiagnose daemon failure.
7. **Does v6 solve Work Web staleness or cross-repository semantics?** No. These are explicit dependent initiatives with separate ownership and independent acceptance; do not hide them under a green bridge status.
8. **Can background execution substitute for Buork continuity?** No. The bridge preserves jobs and results; work objectives, model reasoning and session continuity belong above it.
9. **Could integrating this plan overwrite local work?** No. Publication uses a dedicated job/worktree from verified remote `main`; the dirty pre-existing branch and ignored files are out of scope.
10. **What remains unverified today?** Current remote state after this document's integration, source-level code execution of the proposed tests, staged write and approval behavior, workspace finish under failures, actual private-repo branch-protection eligibility, and production cutover. These are release gates, not achievements.

## 9. Primary evidence pointers (clean canonical URLs)

- Astra, *GitHub ecosystem audit: Agent instructions, Work Web, bridges and continuity*, 2026-10-09, pages 1-14. Source attached to the planning conversation. The findings above include enough evidence and reproduction detail to work without the original attachment; consult it for the broader ecosystem context.
- [Workspace source baseline](https://github.com/krahd/llm-git-bridge/blob/87a1e8bcfa736ab546b95b00cf81845958c301ed/shell_bridge/workspace.py); [executor source baseline](https://github.com/krahd/llm-git-bridge/blob/87a1e8bcfa736ab546b95b00cf81845958c301ed/shell_bridge/bridge.py); [existing cutover plan](https://github.com/krahd/llm-git-bridge/blob/87a1e8bcfa736ab546b95b00cf81845958c301ed/docs/V6-CUTOVER-PLAN-2026-10-05.md).
- [Work Web source at Astra-reviewed revision](https://github.com/krahd/tom-work-admin/blob/1d08a822891f8b0f23837062c8bf33c8eea92cca/scripts/work_web.py); [Work Web edit transaction](https://github.com/krahd/tom-work-admin/blob/1d08a822891f8b0f23837062c8bf33c8eea92cca/scripts/work_edit.py).
- [Git worktree behavior](https://git-scm.com/docs/git-worktree); [Git reflog expiry and limits](https://git-scm.com/docs/git-reflog); [GitHub branch protection](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).
- [AWS idempotent API retry guidance](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/); [AWS transactional outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html).

---

**Decision summary:** preserve the current daemon, Drive transport and coordinator architecture; repair destructive GC, authorization and durability first; make canonical completion a tested operation; validate from restricted Edu; qualify the exact build; migrate reversibly. No new platform until evidence requires one.
