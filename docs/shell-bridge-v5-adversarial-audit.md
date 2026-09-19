# ChatGPT Shell Bridge v5 — Adversarial Architecture Audit

Status: ITERATION 4 CLEAN FOR IMPLEMENTATION PLANNING
Date: 2026-09-18

## Iteration 1 — repository concurrency model

### Findings
- Repository-wide mutation locks would make `research/` unusable because logically unrelated papers share one repository.
- Per-request unrestricted execution against canonical checkouts allows conversations to corrupt each other's editing state.
- Conversation death can strand unpublished changes.
- Same-file Git conflict detection alone cannot detect semantically incompatible edits to one paper/resource.

### Repairs
- Isolated branch + linked worktree per durable job.
- No repository-wide editing lock.
- Job-local execution lock only.
- Short target-branch integration lock.
- Mandatory logical resource key plus changed-path overlap fallback.
- Interrupted jobs remain durable and never auto-integrate.

## Iteration 2 — durability and canonical integration

### Findings
- A worktree that survives locally is insufficient if the Mac disk fails or cleanup occurs before recovery.
- Merely checkpointing at conversation boundaries is unreliable because context exhaustion can be abrupt.
- Updating the canonical checkout directly creates unnecessary coupling to unrelated local dirt.
- Locking locally cannot prevent an external GitHub push during integration.

### Repairs
- Successful mutating job commands auto-create WIP checkpoint commits and push/verify the private job branch.
- Integration uses a temporary worktree from exact remote target, never the canonical editing checkout.
- Immediate pre-push fetch/revalidation plus ordinary non-fast-forward push semantics.
- Canonical result verified independently with `ls-remote` before integrated state is recorded.
- Private branches retained after integration for a recovery window.

## Iteration 3 — crash/replay and transport

### Findings
- v4 correctly refuses to replay STARTED requests but a hard daemon death can leave the spawned command process group alive.
- v4 rclone calls have no timeout and can wedge the only daemon loop indefinitely.
- Human-readable Drive folder names are ambiguous across accessible Drive roots; this already occurred in production.
- Concurrent execution can cause duplicate polling of an active request unless admission tracks active IDs.
- Concurrent rclone calls would add a new failure surface without demonstrated need.

### Repairs
- Durable `active.json` PID/PGID identity, graceful shutdown containment, and startup orphan sweep before indeterminate publication.
- Hard rclone timeout.
- Exact Drive root-folder-ID pinning.
- Active request set excludes duplicate dispatch.
- Command execution concurrent; Drive transport remains serialized initially.
- STARTED is persisted only at dispatch, so backpressured requests remain safely unstarted.

## Iteration 4 — ecosystem integrity and metadata loss

### Findings
- Local job metadata can itself be stale, lost, or crash between Git mutation and state update.
- A crash after successful GitHub main push but before `integrated` metadata would make a completed job appear pending.
- Two jobs may use different resource keys accidentally while changing the same files.
- Cross-repository updates cannot honestly be atomic using independent Git pushes.
- Worktree/branch cleanup could destroy the evidence required to recover a half-finished conversation.
- Raw shell authority means safety cannot be enforced solely by command inspection without ceasing to be a transparent tunnel.

### Repairs
- Coordinator state is reconstructable from Git worktrees, private branches, remote refs and canonical integration trailers.
- Recovery verifies GitHub target to repair post-push/pre-metadata crashes.
- Changed-path intersection is a second collision detector independent of resource keys.
- Cross-repository workflows use publish-source-first dependency ordering and durable recovery, not false atomicity.
- GC requires verified integration + clean state + retention expiry and refuses indeterminate jobs.
- Workspace orchestration is the standard mutation path in the client skill; raw shell bypass remains an explicit privileged trust boundary.

## Failure-scenario matrix after repairs

| Scenario | Expected behaviour | Data/canonical risk |
|---|---|---|
| Conversation runs out of context | worktree + private branch remain; last successful mutation checkpoint is pushed | no canonical mutation; in-flight request may be indeterminate |
| Two conversations edit different papers in `research/` | separate worktrees execute concurrently and integrate independently | none if resources/paths distinct |
| Two conversations edit same paper | both may work privately; second integration stops for resource/path reconciliation | no silent semantic merge |
| Two commands hit same job concurrently | job execution lock serializes them | no worktree race |
| Daemon receives SIGTERM | admission stops; active process groups contained; terminal state persisted | no replay |
| Daemon is killed hard | restart sweeps recorded active PGIDs; STARTED request becomes indeterminate | partial command effects preserved for inspection, never replayed |
| rclone hangs | subprocess timeout returns control; polling recovers later | no daemon-wide permanent wedge |
| Drive contains duplicate folder names | runtime uses exact root ID | no mailbox redirection |
| Result upload fails after command | FINISHED/local result already durable; later poll republishes | no command re-execution |
| GitHub main moves during integration | final revalidation/push rejects; candidate discarded/reconciled | no overwrite |
| Crash after main push before local metadata | recovery observes exact remote integration commit/trailer and marks integrated | no duplicate integration |
| Job metadata file disappears | reconstruct from worktree/branch/remote/trailers where unambiguous | recoverable; ambiguous cases fail closed |
| Worktree directory disappears but checkpoint branch is pushed | recreate worktree from private remote branch | checkpoint work survives |
| Local disk fails after verified checkpoint push | recreate from remote private branch | changes through checkpoint survive |
| Cross-repo source succeeds, admin update fails | source remains canonical; dependency journal shows missing admin follow-up | temporary omission, never false future reference |
| GC runs while job dirty/pending | GC refuses job | no work loss |
| Client intentionally bypasses workspace via raw shell | outside orchestration guarantees | explicit privileged trust boundary |

## Residual risks judged non-material to architecture correctness

1. The raw-shell capability can deliberately bypass workspace discipline; this is inherent in the product's chosen trust model and must remain explicit in the skill/security docs.
2. There is a tiny crash window between OS process creation and durable PID/PGID recording. Replay is still prevented by STARTED; orphan containment may require manual inspection in this exceptional window.
3. Local work newer than the last verified private-branch checkpoint is vulnerable to total Mac disk loss. Conversation/daemon death alone does not lose it because the worktree persists. Successful mutating requests minimise this window by automatic checkpointing.
4. Cross-repository GitHub updates are recoverable, ordered operations rather than mathematically atomic distributed transactions.
5. The active rclone remote currently uses the shared Google Drive OAuth client whose retirement warning is external credential/infrastructure debt. v5 detects and reports it; provisioning a private client requires user/account action.

## Convergence result

A fifth architecture attack was run against: concurrent same-repository work, same-resource semantic collisions, conversation disappearance, local metadata loss, daemon/process death, transport hang, duplicate Drive naming, publication ambiguity, remote branch races, cleanup, and cross-repository dependency ordering.

No new material architectural weakness was found beyond the explicit residual trust/credential/distributed-transaction limits above. The architecture is therefore sufficiently converged to create an implementation plan. Any implementation defect discovered by tests reopens the relevant design assumption rather than being papered over.
