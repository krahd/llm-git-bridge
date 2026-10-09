# Local Executor Bridge v6: transparent execution audit and implementation plan

**Status:** revised implementation contract (adversarial review 2026-10-09); **not** a production release authorization or a certification that safeguards are deployed.  
**Prepared:** 2026-10-09 UTC. **Owner:** `krahd/llm-git-bridge`.  
**Baseline:** GitHub `main` observed and used by the durable workspace coordinator at `87a1e8bcfa736ab546b95b00cf81845958c301ed` (2026-10-09 06:02 UTC).  
**Input:** *GitHub ecosystem audit: Agent instructions, Work Web, bridges and continuity* (Astra, 2026-10-09, 14 pages; supplied file SHA-256 `bfd747b17b1a3fae692c06982ef78c180918090235814eaa6b2a4ae7d8e9f672`), plus read-only bridge/source/runtime inspection from an ordinary ChatGPT Edu conversation (2026-10-09 05:50-05:57 UTC).  
**Intended result:** one v6 production local executor, with proven ordinary-Edu operation, no routine approval fatigue, conservative preservation, durable jobs, and independently verified GitHub completion.

> **Reading rule:** This plan describes **desired future behavior** unless explicitly marked *observed* or *tested*. Do not infer that staging has passed an acceptance gate merely because a feature exists in source or the service is running. Preserve v5 production until a qualified v6 cutover and rollback test. Use this document for release sequencing, but carry forward the explicitly retained regression requirements of the 2026-10-05 cutover plan (Section 4.5 below). Historical plans remain evidence, not alternative instructions.

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
| G1 / P0 **reproduced externally** | Astra's disposable-repo test: GC deleted a remote job branch after that branch gained a later, unintegrated commit; the new commit remained locally reachable in the test, but its remote recovery ref disappeared. Source: `shell_bridge/workspace.py` near `gc_jobs`, `87a1e8bc…`. | Not proof of permanent data loss on production; proof that current branch-deletion logic is unsafe. | **Freeze all destructive GC effects, including local branch/worktree and metadata removal**, in every callable installed coordinator until repaired and qualified. |
| G2 / P0 for unattended cleanup **reproduced externally** | GC attempted removal of a locked worktree, ignored errors, and deleted job metadata while the worktree and branch remained. | Recovery index can be erased while actual resources survive. | Check all removal outcomes; preserve terminal receipts; no false success. |
| I1 / P1 source reviewed | `integrate_job` runs validation only when provided by the caller; it accepts states beyond `ready`. There is no uniform mandatory validation for all publication routes. | Green local tests are not an enforced canonical admission policy. | Trusted validation contract, candidate-bound results, CI on `main`, and publisher parity. |
| I2 / P1 source reviewed | The coordinator has checkpoint and integration mechanisms, but does not offer a single durable, recoverable `finish` contract; a crash after push but before recording remains a reconciliation risk. | Repeating the integration may create duplicate semantic effects; a clean workspace alone is insufficient. | Receipt-aware, idempotent completion and same-job locking. |
| A1 / P0 source reviewed | The v6 menu review path may authorize a request without the exact command being displayed in the menu; `operator_confirmation_mode=off` remains capable of auto-allowing an otherwise required approval. | Convenience configuration can defeat human-required decisions. | Require explicit command/effect inspection for review; `off` means **reject elevated operations**, never allow. |
| A2 / P0 **policy enforcement gap** | `AGENTS.md` forbids all agent-created public repositories and agent-induced public visibility changes, even with operator approval. Yet broad `system` shell can reach network and user credentials and source detection partly uses regexes. | A disclaimer cannot turn a bypassable human-only prohibition into an enforced boundary. | Keep prohibited capabilities unavailable through every agent-accessible profile, including elevated ones. If unrestricted `system` cannot enforce that, **disable that profile for agent requests** until credential/network isolation is proven. Human-only visibility changes occur outside the agent executor. |
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

A small client wrapper or skill must expose **submit, inspect, resume**, hiding the mechanics of JSON construction/upload and result lookup. It must use the pinned mailbox identity discovered in a verified runtime manifest, generate a stable unique request ID, and preserve the exact request hash, write scope, explanation, and operation IDs in durable records. Exact result lookup takes precedence over broad Drive searching. After ambiguous delivery, inspect the original ID. **A "not started" snapshot does not authorize a new-ID replacement:** the original file may arrive later. Make the daemon atomically deduplicate the stable *logical operation ID + canonical payload hash* across all transport submission IDs, including simultaneous deliveries, restart, and different clients; same ID with different bytes is a conflict. Persist the admission record before starting effects and make terminal history immutable. If implementing that contract is impractical, use one immutable request ID and never send a replacement mutation until the original is durably tombstoned/cancelled at the admission boundary and late delivery is rejected. A mere connector deletion or client-side cancelled label is not sufficient. External effects are not promised exactly-once; ambiguous non-idempotent effects require reconciliation.

Preflight verifies connector *read and write* capability in a fresh restricted Edu chat, mailbox identity, runtime version and supported scopes. If the file-management tool is unavailable in that context, fail with a concrete transport capability error rather than claiming Mac execution failed. The bridge cannot guarantee that all Edu conversations expose identical tools; this is an explicit acceptance test, not an assumption.

### 3.2 Runtime and privilege boundaries

Retain `read_only`, `repository`, `system`, `auto` as backward-compatible wire scopes, but keep the UI/skill simple: ordinary authorized work, elevated reviewed operation, or rejected. The client cannot grant itself capabilities by asking for `system`. Filesystem controls are operating-system-enforced and approved workspace roots must include legitimate worktree paths; validate symlinks and actual resolved roots. Raw repository shell stays network-denied by default; trusted Git coordinator operations use narrowly scoped credentials/network and known targets.

There are **three disjoint outcomes**: (1) authorized routine effects run automatically inside enforced scope; (2) eligible consequential effects require bounded human approval; (3) prohibited effects are rejected regardless of approval. The non-waivable prohibitions in `AGENTS.md` (agent-created public repositories; making any repository public) remain human-only outside agent execution. Reject direct, indirect, wrapper, API, and privileged-shell pathways, not merely known `gh` command strings. An unrestricted `system` shell with network and user GitHub credentials cannot reliably enforce this policy; do not expose that capability to agent requests until an OS/credential-isolated boundary or constrained trusted API makes the prohibition enforceable. Approval never overrides prohibited authority; record any remaining unproven guarantees as blockers, not accepted residual risk.

On macOS, required elevation is a real, local decision: display exact `cwd`, command (inspectable without truncation), resource roots, network/capability scope, agent-provided intent clearly marked as *unverified*, and relevant effect warning. An **authentic human decision** must originate through a trusted local helper/decision channel that requesting jobs cannot write, forge or replay. Cryptographic authentication or OS-enforced isolation proves provenance; a plain hash only binds bytes and is **not authorization**. Separately bind the decision to canonical payload hash, logical operation ID, transport request ID, exact cwd/command, scope, nonce and expiry. Test that arbitrary sandboxed jobs cannot write/read the decision store or signing key. No default Allow, no silent bypass, denial/expiry fail closed. Menubar notifications are quiet until exceptional review; an uninspected summary must not expose an Allow action. `off`/unavailable UI **denies** required elevation; it does not disable safety.

### 3.3 Durable execution and completion

Do not confuse **request result** with **project completion**. Use the existing journal, workspace metadata, Git commit trailers and narrow durable receipts; avoid a new all-purpose state store. One operation can move through these independently evidenced states:

`submitted → admitted → started → finished` (shell lifecycle); and, only for Git work, `checkpoint_pushed → candidate_validated → integrated_remote → remote_verified → terminal_receipt`. Admission is atomic by *logical operation ID + canonical payload hash*, not by transport filename alone. A terminal receipt is an immutable **historical event** bound to exact job/operation IDs, candidate tree, validator policy revision, verified integration commit/ref and observation time. Subsequent edits, reversions and superseding commits are separately reported events; they do not invalidate the earlier receipt or permit replay.

For cross-repository consequences, a separate small owner-directed completion record may additionally track `admin_reconciled` and `view_visible`; a bridge transaction must never invent semantic owners. Report a partial state honestly and return the unresolved obligations rather than collapsing every step into `success`.

The executor must preserve the original request ID and its output/reconciliation identity across provider-session loss. The bridge should not attempt to recreate lost ChatGPT reasoning; that is Buork's layer.

## 4. Implementation programme and stop/go gates

Each phase is a bounded change in a dedicated workspace based on **fresh verified GitHub `main`**. Checkpoints are pushed. Integration into canonical `main` is serialised and independently verified. No phase performs destructive cleanup to satisfy an aesthetic branch count.

### Phase 0 — freeze unsafe cleanup and reconcile state (P0; prerequisite)

**Change:** Immediately prohibit invocation of **all destructive `gc` paths** from every client, not merely remote branch deletion; inventory and reconcile any in-flight GC. Then implement a fail-closed, default-disabled GC guard in `shell_bridge/workspace.py` covering worktree removal, local and remote ref deletion, lock changes that enable deletion, and job metadata removal. Verify the guard in **every callable installed coordinator** (including production v5 and isolated staging v6) against exact binary/source hash. A GitHub commit alone does not protect deployed copies. If a deployed coordinator cannot be safely guarded without disturbing active v5, prevent its clients from invoking `gc` and mark the residual exposure explicitly blocked. Check for any current executing GC before patching. Re-read canonical `main` and current job metadata with an isolated workspace; preserve the dirty `ai/installer-approval-integrity-20261003-a1` checkout and ignored `.worktrees-v6/` contents. Record build and runtime versions separately.

**Gate:** no supported or installed client route can destructively GC **any** job artifact or erase metadata during the freeze. Demonstrate on actual installed versions or safely isolate the unpatched caller. No existing worktree, local-only change or ref is reset, cleaned or force-pushed. If the preflight finds an in-flight GC, reconcile its exact operation before another cleanup action.

**Recovery:** leave all GC destructive behavior disabled, preserve the job, receipt, metadata, worktrees and refs, report exact unresolved IDs. Do not migrate yet.

### Phase 1 — repair workspace preservation and integration foundations (P0/P1)

**Files:** `shell_bridge/workspace.py`, focused `shell_bridge/tests/test_workspace.py` plus one minimal integration test file only if needed.

**Change:** GC eligibility requires `integrated`/terminal state, same-job lock, inactive process, worktree cleanliness, recorded checkpoint, equality of both local and freshly fetched remote job tips with the expected preserved checkpoint, and evidence that its content reached the verified canonical integration. Protect exceptional historical artifacts with a named preservation/archive ref. Remove locked worktrees only after deliberate verified unlock; check each subprocess return code, verify absence, and retain a terminal receipt even on success. Remote deletion must be conditional on the expected remote ref (atomic compare-and-delete / lease), and never run after a mismatch. After repair, destruction remains an explicit separately enabled maintenance capability, never implicit in `finish`, and must be safely disabled by default. Acquire the same per-job lock around `ready`, `integrate`, and `gc`; re-read state under lock. Do not make `git worktree remove --force` a default cleanup strategy.

**Adversarial tests:** new commit on integrated branch, concurrent remote branch advance between check and deletion, failed unlock/removal, dirty and untracked files, missed job metadata, crash between push and recording, stale target, and missing user-owned preservation refs. Require surviving remote ref + job record whenever a test cannot prove deletion safe. Use disposable bare remotes; do not simulate by deleting real remote branches.

**Gate:** both Astra reproductions now fail safely, no false GC success, no unique remote checkpoint can be erased under tested races. If it fails, leave **all destructive GC** disabled while unrelated fixes proceed.

### Phase 2 — authorization, approvals, and sandbox usability (P0)

**Files:** `shell_bridge/bridge.py`, native `shell_bridge/approval_gui.swift`, approval helper, and their focused tests.

**Change:** remove `off` → allow semantics, fail closed when required approval cannot be collected, require human inspection of exact command/effect, establish an **unforgeable local human-decision channel** (hash binding is not signing), and demonstrate that unsafe subprocess/network paths cannot escape the claimed capability boundary. Treat agent-created public repositories and changes to public visibility as unconditional denial even after an approval. If arbitrary `system` cannot preserve that prohibition, do not grant arbitrary `system` to agents; route bounded eligible exceptions via capability-specific trusted helpers. Fix allowlisted workspace roots that currently block normal worktree creation; test nested worktrees and symlink resolution. Keep routine authorized edits and builds approval-free.

**Adversarial tests:** disguised high-impact commands, shell wrappers, redirection/symlinks, script invocation, malformed request/cwd, prohibited visibility changes via `gh`, `git`, direct GitHub API and alternate network routes, false helper decisions, decision-store write attempts, stale or replayed approval, UI not running, denied/expired approval, and user-visible review that actually reveals command bytes. The tests must distinguish **detected** command patterns from **enforced** privilege boundaries. Where an effect cannot be bounded (e.g. unrestricted `system` network shell with user credentials), **refuse agent execution** if it can violate a mandatory prohibition; do not exchange a warning or click for an unenforceable invariant. For other eligible high-impact but enforceably bounded effects, require explicit elevation.

**Gate:** zero approval popups for a defined routine test corpus, all required elevation either explicitly authorized for the bound payload or rejected, and successful actual UI smoke on the Mac desktop (not solely unit tests).

### Phase 2.5 — admission and retry race closure (P0; before unattended execution)

**Change:** make request admission use one durable logical-operation identity and canonical payload digest, independent of Drive file IDs. Under an atomic daemon-owned lock/transaction, register the operation as accepted before execution; duplicates with identical bytes return its existing status/result and duplicates with conflicting bytes fail closed. A delayed original and a replacement submitted concurrently must never both START. If the previous wire protocol cannot represent this safely, preserve the original ID and prohibit replacement until an explicit admission-level tombstone is durable; do not infer absence from mailbox lists. Implement stable immutable results and bounded status lookup for cross-conversation recovery.

**Tests and gate:** reordered Drive uploads, delayed first delivery, simultaneous identical and conflicting submissions, crash before/after durable admission, daemon restart, and two clients using different transport IDs for the same logical operation. Confirm exactly **one admission/start** under controlled faults, without claiming exactly-once external side effects. Inspect daemon state, not client-reported absence, to decide retry eligibility.

### Phase 3 — reliable `finish`, mandatory validation and GitHub admission (P1)

**Files:** coordinator + tests; minimum trusted per-repository validation configuration; main-branch CI.

**Change:** implement idempotent `finish(job_id)` as one **durable logical operation** composed of checkpoint, ready, validation, integration, independent remote verification, and terminal receipt. A validated candidate must be the exact tree pushed. Validation requirements must come from a **trusted policy revision pinned independently of the candidate** (e.g., protected canonical configuration at a verified base commit plus approved central minimum), never from code or policy simultaneously changed by the candidate being admitted. Record validator definition/hash, baseline policy revision, commands, environment, exact tree, result and expiry; reject attempted self-downgrades. Extra client checks may strengthen, never weaken, the gate. An unknown or missing required validator blocks **publication**, preserving the candidate. Use the existing resource/path-overlap model; auto-reconcile only proven disjoint changes, revalidate after a changed target, and preserve both alternatives on semantic conflicts. After ambiguous push, inspect commit trailers/remote ancestry and the durable operation receipt to identify already-completed integration, including squash/advanced-main cases; do not push a duplicate effect. Once remotely verified, preserve the historical receipt forever (subject to normal audit-record retention), even if later commits remove or change the original content. Subsequent revision state is reported separately. Cleanup is optional and **never** part of the success gate.

Install bridge qualification checks on canonical `main`, not solely a historical feature branch. Add branch/ruleset protections only after verifying the GitHub plan's eligibility and adapting all existing publishers; a mandatory human PR review must not be imposed on routine agent work. A local `finish` alone cannot enforce validation for direct GitHub/Work Web edits: record that external-policy gap separately until equivalent admission checks exist.

**Gate:** fresh/overlapping writers, changed target, missing or self-downgraded validator, stale-base, crash immediately after remote push, and `main` advancing or reverting content immediately after success all lead to a correct **immutable** terminal receipt or a durable blocked state. Canonical ref is independently observed and matching content is confirmed, not inferred from local HEAD.

### Phase 4 — restricted-Edu client reliability and diagnostics (P1)

**Change:** update the installed `mac-git-bridge` skill/client guidance to use the **verified** file-management raw-upload route, exact result retrieval, bounded request/recovery procedures and observed runtime manifests. Publish distinct `desired_source`, `installed_build`, `active_mailbox`, `protocol`, `capabilities`, `acceptance_revision/time` values. Classify failures by connector, transport, daemon, sandbox, approval, workspace, Git remote, and conversation delivery. Avoid broad mailbox polling and repeated rescans.

**Gate:** in at least two **new** ordinary Edu conversations, successful read-only and authorized-write operations; one lost-result recovery by ID; unambiguous rejection of unsupported capabilities; no hidden operator confirmation for normal authorized work. Report request round-trip duration separately from daemon command duration. The previously observed 0.043-second staged smoke is not an SLA.

### Phase 5 — qualify staging, cut over once, and retire verified legacy services (P1)

**Change:** stage the exact latest integrated commit with manifest hashes for daemon/coordinator/helper/native app/config/LaunchAgent. Validate from the real Mac GUI session, and run representative shell, workspace, approval, GitHub, timeout, restart and rollback scenarios. Verify v6 staging cannot consume v5 production mailbox. Require a signed or otherwise authenticated release receipt stating the exact passed candidate and acceptance results. Make cutover provisional until production acceptance and rollback viability are independently verified. Stop old consumers **only** as part of a reversible transaction; preserve backups and pending IDs. Retire inactive historical LaunchAgents only after identifying ownership and proving they are not used.

**Gate:** one and only one service consumes the production mailbox, enforced by an **exclusive ownership lock keyed to the canonical mailbox/folder ID**, independent of configurable instance names; active health and manifest agree on tested release identity, production smoke includes approvals and GitHub completion, v5 rollback was exercised, and no indeterminate mutation was silently replayed. On a failed gate, **do not blindly restore v5**. First stop admission, drain/contain children, reconcile all admitted operation IDs and journals across schema versions, then restore v5 only if the state can be transferred or both versions share a verified deduplication barrier. Otherwise keep execution paused with read-only inspection and durable evidence until safe forward repair or controlled migration. Test the exact crash window: v6 mutation committed, result publication lost, rollback attempted.

### Phase 5.1 — rollback state-transition protocol (P0; part of Phase 5, not optional)

**Transition:** `staging-qualified → admission-paused → old-consumer-stopped → shared-mailbox-lock-acquired → v6-installed → accepted-or-held`. A production mailbox has one ownership lock keyed to its canonical Drive folder ID, not the configured instance name. Before handover, stop new admissions, inventory pending uploads, drain or contain all child process groups, and snapshot durable STARTED/FINISHED, admitted logical operation IDs, result/outbox and approval decisions. Atomically hand over ownership before either version can consume another request. A stale lock is recoverable only through demonstrated process death plus journal reconciliation, not timeout alone.

**Rollback:** `v6-admission-paused → v6-children-drained/contained → state reconciled → v5-resumed` is permitted only when v5 can recognize all previously admitted effects or a shared compatibility barrier prevents replay. If v6 has a verified side effect but lost its result before publication, publish/reconcile that receipt **before** admitting the same operation under v5. If formats or admission sets cannot be reconciled, enter `execution-held / status-readable` instead of automatically booting v5. Preserve both journals and all incoming uploads; no rollback resets or discards historical completion evidence.

**Adversarial gate:** two different instance IDs configured to one mailbox, simultaneous daemon startup, stale lock, unread upload in flight, crash between mutation and reply, v6→v5 rollback with an indeterminate request, and duplicate request after rollback. One consumer or zero consumers during maintenance is acceptable; two consumers are never acceptable.

### Mandatory compatibility regressions carried forward from 2026-10-05 cutover plan

The earlier cutover plan remains authoritative as a **regression inventory** on the following points even though this plan controls sequence and design: click-only Allow/Reject (no Touch ID, password, `LAContext.evaluatePolicy`, or implicit keyboard default); persistent menu-bar approval queue (popups are not the only review route); bounded concurrency; process-group descendant containment; STARTED/FINISHED crash semantics; maximum request/command/stdin/stdout/stderr/time budgets; rclone/Drive transport timeout and health staleness semantics; macOS wake lease; checkpoint and GitHub independent remote verification; installer hash parity and reversible cutover; and a final current-v5-versus-v6 reliability-fix comparison immediately before integration. Record each as pass/fail/waived-with-justification against the exact staged executable and source commit. No silent regression merely because a newer document omitted a requirement.

### Separate downstream track — Work Web and ecosystem consistency (not a v6 cutover blocker)

Astra identified real Work Web risks: cached freshness, old values paired with newer revision tokens, ephemeral failed-save candidates, inconsistent code/data generations, and central registry changes lacking owner evidence. The owner is `tom-work-admin`; the fix should use immutable data+revision snapshots, baseline-relative field changes, operation IDs, durable saves and narrow reconciliation. Do not turn these into new bridge-side semantic rules. Shared completion receipts can link owner actions and downstream consequences without introducing a shadow registry. Until repaired, Work Web must not advertise its forms as concurrency-safe merely because the bridge is v6. The separate Work Web plan should be owned, tested and integrated in its own repo.

## 5. Acceptance matrix (must be documented against exact build SHA)

| Case | Expected outcome | Release gate |
| --- | --- | --- |
| Delayed original arrives after a new transport submission | One logical operation admitted; no duplicate START, conflicting bytes rejected | Phase 2.5 |
| Agent attempts forbidden public repository creation or public visibility change, including via privileged or wrapped API | Rejected even with approval; no unbounded bypass capability exposed | Phase 2 |
| Agent tries to forge approval decision or tamper with protected helper state | Rejected; authentic human origin is independently checked, not inferred from a hash | Phase 2 |
| Destructive GC requested through any still-installed coordinator during freeze | No worktree, local/remote ref or metadata deleted; actual executable/source version verified | Phase 0 |
| Candidate edits its own mandatory validation policy to remove required checks | Publication blocked or original trusted policy enforced | Phase 3 |
| Two installed daemons with different instance IDs target the same mailbox | At most one admission owner; losing daemon does not execute | Phase 5 |
| Successful v6 mutation, lost result, then rollback to v5 | Effect reconciled; no replay; execution held if compatibility unproven | Phase 5 |
| Click-only approvals and full v5 reliability regression inventory | All retained requirements passed against exact staged release | Phase 4/5 |
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
| `main` later advances, rewrites the file or reverts the change | Original immutable completion receipt remains valid as historical fact; later supersession separately recorded; no replay | Phase 3 |
| Staging → production → rollback | Exclusive mailbox owner, admission barrier and journal reconciliation; v5 restored only if every admitted mutation is accounted for, otherwise execution paused safely with status access | Phase 5 |

For reliability claims, record attempted N, pass/fail, latency distribution, environment and exact version. Do not promise 99% availability based on a handful of smoke tests. Tests are necessary but cannot prove absence of malicious shell escape or semantic project correctness.

## 6. Explicit exclusions and stop conditions

**Not in v6:** finished Buork, Work Web rewrite, cross-repository semantic auto-resolution, monorepo migration, universal schema migration, all 97 repos' policy conversion, bulk branch deletion, an additional cloud control plane, or a second always-on transport. Native MCP remains optional only if actually available in restricted Edu, not a precondition.

**Stop and preserve:** a branch/ref tip changes unexpectedly; a stored request may have executed but has no authoritative result; a validator is absent; sandbox or approval bypass is discovered; the Mac's active runtime does not match the claimed release; GitHub push or protection prerequisites are unavailable. Report the exact job/request, remote ref and last verified checkpoint, never erase recovery state to force completion.

**Safety decision:** removing confirmation fatigue is not unrestricted authority. Non-waivable agent prohibitions remain enforced even after local human approval. Disable unbounded `system` capabilities while a bypass is possible; implement narrowly scoped trusted helpers and OS/credential partitioning or explicitly block the operation. Regex detection is an additional signal, never the enforcement boundary.

## 7. Handoff to the next conversation / persistent executor

**Revision notice:** this document was corrected after a second adversarial review on 2026-10-09. Earlier SHA-256 / commit links to the first release of this plan remain historical. Always read the current canonical `main` version before implementation.

**Canonical source of this plan:** `krahd/llm-git-bridge/docs/plans/BRIDGE-V6-TRANSPARENT-EXECUTION-2026-10-09.md` on GitHub `main` *after verified integration*. Read it from GitHub fresh; do not trust a pasted transcript or an unverified Drive copy. Compare repo/source, local worktree, staged build and production runtime separately.

**Planning job:** `v6-transparent-plan-20261009-a1`, workspace resource `docs/v6-transparent-architecture`, repo `/Users/tom/tom-repos/projects/llm-git-bridge`, created from `87a1e8bcfa736ab546b95b00cf81845958c301ed`. A successor must first check whether this job is already integrated and verify the remote ref; **do not recreate or replay it** just because a chat ended. This ID belongs to the *documentation task*, not to future code changes.

**First implementation action once this plan is on verified `main`:** inspect current `shell_bridge/workspace.py`, all installed coordinator copies, and existing GC tests; create/resume a **new** isolated implementation job scoped to workspace preservation, freeze **all destructive GC** (local worktree/ref, remote ref, job metadata) through each callable installed entry point, and add the two reproduced-failure regression tests. Validate, checkpoint, integrate and verify remote. Then continue through the phases above.

**Other active work:** Preserve the dirty older local checkout `ai/installer-approval-integrity-20261003-a1` (observed modified `shell_bridge/approval_gui.swift`, `tests/test_shell_bridge_installer_portability.py`, and untracked `.worktrees-v6/`). Review before merging its still-unpublished changes; do not reset/clean/stash it. Production was observed as v5, v6 candidate `87a1e8bc…` staged; do not mistake either for a current fact without new inspection.

**Exact Edu smoke evidence:** production v5 audit request `v6-audit-readonly-20261008-2349-a1`, result file ID `1LRUhunGWRIQjh3Y6Z-JUpK-6P0y_xt2N` under the pinned results folder `1SMdHzWEj2w2zRcOsCBc6fGT44ltco1fL`; staged v6 read-only request `v6-audit-stage-smoke-20261008-2359-a9`, result file ID `1x7imAz77f0uFIG2u5amZF7F-NmDRr_s5` under staged results folder `1xnU55UNAc-6APc6b-8naPNyk4DlUC3xd`. Both reported terminal completion and exit 0. These Drive files are *transport execution evidence*, not repository authority and not proof of general write/retry/approval reliability.

**Client route tested in this conversation:** `files__manage_library` upload of a local raw JSON into the pinned production or staging requests folder; Drive `list_folder` and `fetch` of exact result. This is an *observed available route*, not an instruction to assume availability in a new chat. Source filenames, IDs and command bytes for audit probes remain in transport results; GitHub is the authoritative persisted plan.

## 8. Fresh-eyes adversarial QA of the proposal

1. **Could automatic finish delete unfinished work?** Yes unless GC is separately disabled and source/deployed copies are patched; therefore `finish` cannot trigger cleanup and Phase 0 freezes *all* destructive GC, not just remote ref deletion. Phase 1 tests local/remote tip equality, removal failures and lease/race safety. Even a verified integration does not authorize removal of later commits.
2. **Does this plan promise exactly-once shell side effects?** No: it requires durable identity and reconciliation; arbitrary external effects cannot be atomically committed with the bridge journal. Indeterminate non-idempotent actions remain blocked for evidence-based resolution.
3. **Does a regex guarantee GitHub visibility safety?** No. `AGENTS.md` requires a **hard prohibition**, not a mere disclaimer. An agent-visible capability that can bypass this through raw network shell is disallowed until isolation proves the prohibition.
4. **Could mandatory validation make work impossible?** Yes if policy is absent or too strict. Introduce reviewed per-repository minimum checks, verify CI eligibility, keep candidates recoverable when validators are missing; avoid blanket human PR review.
5. **Could a deployment manifest be stale or forged?** Yes if self-reported only. Compare on-disk hashes, launchctl process identity, immutable installation location, exact Git commit, and observed mailbox behavior during release qualification.
6. **Could a fresh Edu conversation lack the upload tool?** Yes. Qualify the client path in fresh conversations and classify missing tools as a platform capability limitation; do not misdiagnose daemon failure.
7. **Does v6 solve Work Web staleness or cross-repository semantics?** No. These are explicit dependent initiatives with separate ownership and independent acceptance; do not hide them under a green bridge status.
8. **Can background execution substitute for Buork continuity?** No. The bridge preserves jobs and results; work objectives, model reasoning and session continuity belong above it.
9. **Could integrating this plan overwrite local work?** No. Publication uses a dedicated job/worktree from verified remote `main`; the dirty pre-existing branch and ignored files are out of scope.
10. **What remains unverified today?** Current remote state after this document's integration, source-level code execution of the proposed tests, staged write and approval behavior, workspace finish under failures, actual private-repo branch-protection eligibility, and production cutover. These are release gates, not achievements.

### Second-review defects and adversarial decision record (2026-10-09)

- **High priority, accepted:** agent-reachable unbounded `system` cannot coexist with a hard prohibition on public-repository changes when GitHub credentials/network are available. Use deny-by-capability, not approved-with-warning.
- **High priority, accepted:** absence of STARTED is not durable cancellation; logical-operation deduplication must exclude late-original/different-ID races.
- **High priority, accepted:** GC has three independently destructive effects (worktree/local ref, remote ref, metadata); disabling only the remote deletion flag does not protect the other two. Verify installed copies.
- **High priority, accepted with correction:** rollback may not always restore v5. Preserving state sometimes requires holding execution until v6/v5 journals and outbox are reconciled.
- **High priority, accepted:** a payload hash is not evidence of authentic human authorization. Require a trusted decision provenance boundary.
- **Accepted:** keep concrete 2026-10-05 parity requirements, not blanket replacement of earlier plans.
- **Accepted:** completed historical work stays completed in its immutable receipt after later edits/reversion.
- **Additional accepted:** validate against trusted, separately pinned policy; include exact smoke evidence IDs. These corrections are design requirements, not evidence that implementations now satisfy them.

## 9. Primary evidence pointers (clean canonical URLs)

- Astra, *GitHub ecosystem audit: Agent instructions, Work Web, bridges and continuity*, 2026-10-09, pages 1-14. Source attached to the planning conversation. The findings above include enough evidence and reproduction detail to work without the original attachment; consult it for the broader ecosystem context.
- [Repository mandatory policy at the inspected integration](https://github.com/krahd/llm-git-bridge/blob/dd4e1655d93c44878c884b4aa1d50976f8130c7a/AGENTS.md): agents must not create public repositories or make repositories public; enforcement rejects, not approves.
- [Workspace source baseline](https://github.com/krahd/llm-git-bridge/blob/87a1e8bcfa736ab546b95b00cf81845958c301ed/shell_bridge/workspace.py); [executor source baseline](https://github.com/krahd/llm-git-bridge/blob/87a1e8bcfa736ab546b95b00cf81845958c301ed/shell_bridge/bridge.py); [existing cutover plan](https://github.com/krahd/llm-git-bridge/blob/87a1e8bcfa736ab546b95b00cf81845958c301ed/docs/V6-CUTOVER-PLAN-2026-10-05.md).
- [Work Web source at Astra-reviewed revision](https://github.com/krahd/tom-work-admin/blob/1d08a822891f8b0f23837062c8bf33c8eea92cca/scripts/work_web.py); [Work Web edit transaction](https://github.com/krahd/tom-work-admin/blob/1d08a822891f8b0f23837062c8bf33c8eea92cca/scripts/work_edit.py).
- [Git worktree behavior](https://git-scm.com/docs/git-worktree); [Git reflog expiry and limits](https://git-scm.com/docs/git-reflog); [GitHub branch protection](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).
- [AWS idempotent API retry guidance](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/); [AWS transactional outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html).

---

**Decision summary (revised):** preserve the daemon, Drive transport and coordinator; first freeze all destructive GC across deployed copies; enforce non-waivable denied capabilities and unforgeable human decisions; close the delayed-upload retry race; implement pinned validation, historical completion receipts and safe finish; qualify the exact build, then migrate only through an exclusive mailbox-owner and journal-compatible transition. No new platform until evidence requires one.
