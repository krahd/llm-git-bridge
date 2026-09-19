# ChatGPT Shell Bridge v5 — Concurrent, Recoverable Workspace Architecture

Status: DESIGN CANDIDATE AFTER ADVERSARIAL ITERATION 4
Date: 2026-09-18

## Objective

Allow many independent ChatGPT conversations to work concurrently across Tomas's repository ecosystem—including many simultaneous jobs inside large repositories such as `research/`—without making conversation lifetime, daemon lifetime, or repository-wide serialization a correctness dependency.

The system must preserve a tunnel-like shell/Git experience while making the *safe default mutation path* durable, isolated, recoverable, and deliberately integrated into canonical GitHub branches.

## Authority

1. GitHub branch refs are canonical published repository state.
2. Mac repositories/worktrees are authoritative for unpublished and in-progress local state.
3. Google Drive is transport and health signalling only; it is never repository truth.
4. A conversation is never a persistence boundary.

## Non-goals

- The raw shell transport is not an OS sandbox and cannot infer shell semantics perfectly.
- Cross-repository GitHub publication cannot be made truly atomic with ordinary Git pushes. Cross-repository workflows therefore use dependency ordering and durable recovery rather than pretending to provide distributed transactions.
- v5 does not auto-integrate interrupted work merely because it is syntactically mergeable.

## Layered architecture

### Layer 1 — Drive transport daemon

The daemon owns one Drive mailbox and remains the only poller/claimer.

Responsibilities:
- pin the mailbox to an exact Google Drive root folder ID;
- poll requests and publish results;
- validate immutable request identity and bounds;
- dispatch accepted requests concurrently;
- supervise each command process group;
- preserve STARTED/FINISHED replay semantics;
- bound rclone operations and execution admission;
- publish health/doctor information.

It does not parse Git commands or attempt to infer repository mutation semantics.

### Layer 2 — workspace coordinator

A local `workspace` helper provides the standard mutation path used by the Mac Git Bridge skill.

Responsibilities:
- create/recover durable jobs;
- create isolated Git branches and linked worktrees;
- serialize execution *within one job worktree* while allowing different jobs in the same repository to run concurrently;
- create automatic WIP checkpoints after successful mutating execution;
- push checkpoint branches for remote durability;
- record resource identity and expected canonical base;
- validate and integrate completed work through a short canonical-branch gate;
- repair/recover state after conversation or daemon failure.

Raw shell remains available for read-only inspection, bridge maintenance, and exceptional recovery. Repository mutation instructions in the client skill use workspace jobs by default.

## Transport concurrency

There is no fixed small worker pool.

Each admitted request receives its own supervised command process. Python supervision may use one lightweight thread per active request, but command execution is one OS process group per request.

A resource admission ceiling remains necessary to prevent fork/process/file-descriptor exhaustion. It is a host-safety backpressure limit, not a semantic worker count:

- config: `max_active_requests = "auto" | integer`;
- `auto` derives a conservative host bound and is capped to prevent pathological process storms;
- excess requests remain unstarted in Drive and therefore replay-safe;
- STARTED is written only immediately before dispatch.

Transport/rclone calls remain serialized behind one transport lock initially. Command execution is concurrent. Every rclone subprocess has a hard timeout.

## Exact mailbox identity

Human-readable Drive paths are not sufficient identity.

Config stores:
- rclone remote;
- exact `drive_root_folder_id`;
- observed requests/results folder IDs for diagnostics;
- bridge instance ID.

Every rclone operation is rooted with `--drive-root-folder-id <exact-id>` and uses paths relative to that root. Installer/setup fails closed on ambiguous discovery and can accept an explicit folder ID.

## Request state machine

Per request durable state:

- `request.json` — exact accepted bytes;
- `started.json` — request SHA, dispatch timestamp, supervisor/process identity when known;
- `active.json` — current PID/PGID plus request token while execution exists;
- `result.json` — terminal result envelope;
- `finished.json` — terminal marker bound to request SHA.

Rules:
1. Same request ID + different bytes is always rejected.
2. FINISHED + matching request republishes exact stored result without execution.
3. STARTED without FINISHED is never re-executed automatically.
4. Before publishing restart-time `indeterminate`, the daemon attempts bounded containment of a still-running recorded process group.
5. Durable result/FINISHED precedes remote publication/deletion.
6. Publication ambiguity therefore causes republish, never re-execution.

## Process lifecycle and daemon death

Normal shutdown:
- stop admission;
- terminate active process groups;
- wait boundedly;
- persist terminal/indeterminate state;
- exit.

Hard daemon death:
- launchd restarts the daemon;
- startup scans STARTED-without-FINISHED records;
- recent recorded PID/PGID identities are inspected and contained when they still match the request execution;
- requests are marked `indeterminate`; they are never replayed.

This narrows the v4 orphan-process gap. A machine/power failure remains semantically indeterminate, as it must.

## Workspace job model

A job is the durable unit of conversational work.

Required metadata:
- job ID;
- repository path and stable repository identity;
- target remote and canonical target branch;
- base commit observed at job creation;
- private job branch;
- linked worktree path;
- resource key;
- last local checkpoint;
- last verified remote checkpoint;
- state (`active`, `interrupted`, `ready`, `integrating`, `integrated`, `conflicted`, `abandoned`);
- timestamps and recovery notes.

State is stored atomically under the shell bridge state directory. It is advisory/reconstructable: Git branches, worktrees and remote refs remain the stronger evidence for repository content.

## Job creation

1. Resolve the exact repository and remote.
2. Fetch the target remote branch.
3. Record exact target commit as `base_commit`.
4. Generate a collision-resistant job ID/branch; never reset/reuse an existing branch.
5. Create a linked worktree with `git worktree add --lock` from the exact fetched commit.
6. Persist job metadata.
7. Optionally push the initial private branch so the job identity exists remotely even before content changes.

The canonical checkout is never used as the editing surface.

## Job execution and checkpoints

Commands for a job execute only after acquiring that job's local execution lock. Two commands never concurrently mutate the same job worktree.

Different jobs—even in the same repository—can execute concurrently.

After a successful mutating command:
1. inspect worktree status;
2. if tracked/untracked changes exist, create a private WIP checkpoint commit (`--no-verify` is permitted for checkpoint durability only; integration validation remains authoritative);
3. push the private job branch;
4. verify the remote branch points to the checkpoint;
5. update durable job metadata.

A failure/timeout/indeterminate command does not auto-commit. The worktree is preserved for recovery and must be inspected before further mutation.

This makes conversation death after any successfully completed mutation lose at most an in-flight request, not all prior work.

## Resource identity and sibling work

Repository identity is deliberately not the unit of serialization.

Every mutating job has a resource key describing the logical work unit, for example:
- `research:paper:testimony-image-power`;
- `research:paper:uruguay-new-media-history`;
- `tom-work-admin:registry:deadlines`;
- `tom-work-admin:ecosystem`.

Resource keys do not block independent editing. They are used at integration/reconciliation time.

The client skill chooses the narrowest defensible logical resource. If it cannot determine one safely, it uses a conservative repository-level resource key.

## Integration gate

Integration is short-lived and serialized per `(repository, target branch)`. Optional named ecosystem/resource locks can also be acquired in deterministic lexical order for genuinely shared integration state.

The job's worktree is not merged directly into the canonical checkout.

Integration algorithm:
1. acquire target-branch integration lock (and any required named locks);
2. fetch remote target;
3. inspect exact current remote target commit;
4. compare it with the job's recorded base and checkpoint;
5. inspect commits integrated since the job base;
6. detect sibling-resource overlap from integration trailers and changed-path intersection;
7. if same-resource or overlapping-path work appeared since the base, stop with `reconcile_required` unless the job has explicitly been reconciled against that exact target;
8. create a temporary integration worktree from the exact remote target;
9. apply the job as a squash candidate, preserving the private checkpoint history separately;
10. run configured validation in the integration worktree;
11. create one canonical integration commit containing trailers for job ID, resource key, base and checkpoint;
12. fetch/revalidate remote target immediately before push;
13. if remote target moved, discard the candidate and return to reconciliation; never force;
14. push the candidate as a normal fast-forward update;
15. verify with `git ls-remote` that the remote target is the expected commit;
16. mark the job integrated and record the canonical commit;
17. remove the temporary integration worktree;
18. release locks.

Private job worktree/branch cleanup occurs only after verified integration and a retention policy; never as a side effect of conversation disappearance.

## Semantic conflict protection

Git line conflicts are insufficient for long-form papers and ecosystem registries.

The integration gate therefore fails closed when either:
- a canonical commit since the job base carries the same `Bridge-Resource` trailer; or
- files changed by the job intersect files changed on the canonical target since the base.

A new conversation may reconcile the job against the latest canonical target, review combined semantics, checkpoint that reconciliation, and record `reconciled_target=<exact commit>`. Only then can integration proceed.

This prevents two conversations editing different portions of the same paper/resource from being silently auto-combined merely because Git can merge them.

## Integration commit provenance

Canonical integration commits include machine-readable trailers:

- `Bridge-Job-ID:`
- `Bridge-Resource:`
- `Bridge-Base:`
- `Bridge-Checkpoint:`

These trailers make resource history reconstructable even if local coordinator metadata is lost.

## Interrupted conversations and recovery

No job is automatically merged after client disappearance.

Recovery scans:
- durable job metadata;
- `git worktree list --porcelain -z`;
- private job branches;
- remote job refs;
- canonical integration trailers.

It classifies each job as recoverable/integrated/conflicted/orphaned and repairs metadata where evidence is unambiguous.

A new conversation can resume the same worktree/branch, including uncommitted local changes after an indeterminate request.

## Cross-repository ecosystem changes

True atomic multi-repository GitHub publication is not claimed.

For a workflow that updates a source repository and then `tom-work-admin`:
1. integrate and verify the source repository first;
2. create/update the admin job referencing the exact published source commit;
3. integrate the admin/index update second;
4. durable workflow metadata records dependencies so recovery can finish a partially completed sequence.

This ordering permits a temporary missing index update but prevents the central registry from claiming an unpublished future source state.

Multi-repository integrations that require several named locks acquire them in deterministic order to avoid deadlock, but remote publication still uses recoverable ordered commits rather than a false distributed-transaction guarantee.

## Health and doctor

`doctor` reports machine-readable:
- bridge version/protocol;
- instance ID;
- source/runtime hashes;
- exact Drive root/requests/results IDs;
- rclone version/connectivity and timeout status;
- detection of the retiring shared Google Drive OAuth client warning;
- daemon PID/start time;
- active request count/admission bound;
- queue/poll state;
- stale STARTED/indeterminate counts;
- workspace job counts by state;
- allowed root;
- no tokens/secrets.

A bounded `health.json` is periodically published at the pinned Drive root. A stale heartbeat is diagnosable even when requests stop being consumed.

## Drive/OAuth robustness

All rclone subprocesses have a configurable hard timeout. Read/list failures are retried on subsequent polls. Ambiguous writes are reconciled against durable local state before retry.

The installer and doctor surface rclone's 2026 shared-client retirement warning. Private OAuth client provisioning remains an external credential action; v5 must not hide this maintenance risk.

## Cleanup and retention

- one live Drive folder may retain the exact name `ChatGPT Shell Bridge`;
- runtime pins its exact folder ID, so names are not authority;
- package/releases live in a separately named release archive;
- obsolete mailboxes are archived/deprecated, never polled;
- private job branches/worktrees remain until verified integration plus retention expiry;
- GC refuses dirty, unintegrated, indeterminate or remotely-unverified jobs.

## Safety invariants

1. Conversation death never deletes work.
2. Daemon restart never replays STARTED work.
3. Canonical branches are not editing surfaces for normal conversation jobs.
4. Different jobs in one repository may execute concurrently.
5. Commands within one job are serialized.
6. Canonical integration of one target branch is serialized locally and guarded by remote fast-forward semantics.
7. Same-resource/path-overlap sibling work requires explicit reconciliation.
8. No force push is used for canonical integration.
9. A job is not declared integrated until the GitHub ref is independently verified.
10. Cleanup never precedes verified integration/recovery classification.
11. Raw shell remains privileged and can bypass workspace conventions; the Mac Git Bridge skill must route ordinary mutations through workspace jobs. This is an explicit trust boundary, not hidden enforcement.

## Acceptance model

The design is acceptable only after deterministic tests/fault injection prove:
- concurrent different jobs in the same repo overlap in wall-clock execution;
- same job commands do not overlap;
- several `research/` paper jobs integrate independently;
- same-resource sibling integration is rejected until reconciliation;
- path-overlap fallback catches undeclared sibling collisions;
- conversation disappearance leaves recoverable work;
- daemon SIGTERM and simulated abrupt restart never replay a STARTED request;
- recorded active process groups are contained on restart where still alive;
- result publication failure republishes without execution;
- rclone hang is bounded;
- Drive duplicate-name ambiguity cannot redirect a pinned runtime;
- remote target movement during integration causes fail-closed retry/reconcile, never overwrite;
- crash after canonical push but before metadata update is repaired by remote verification;
- dirty/unintegrated jobs survive GC;
- full legacy v4 regression suite remains green.
