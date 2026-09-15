# v1.0.0rc4 concurrent-architecture audit

Date: 2026-09-13

This audit closes the multi-repository concurrency programme against the frozen
`0.3.0` single-thread semantic oracle. It is intentionally adversarial: the
release gate is not "threads exist", but that concurrency cannot weaken replay,
stale-base, repository-isolation, durable-result, command, transport, or resource
bounds.

## Scope and fixed invariants

The audited design retains one watcher/mailbox owner and parallelises only local
transaction execution. Drive/rclone I/O, result signing and persistence,
publication markers, registry mutation, metrics, reconciliation, materialisation,
and retention remain watcher-owned. Worker tasks contain frozen local inputs and
never receive the transport object or mutable registry/config objects.

At most one mutating transaction may be active for a resolved canonical repository
path. `max_workers` is fixed and validated from 1 through 8. The watcher-owned
validated backlog is separately bounded by `max_pending_jobs` from 1 through 32
and may not be lower than the worker count. The migration default remains one
worker; two workers/eight pending jobs is the recommended first concurrent setting.

## Adversarial finding fixed after C1

The first C1 implementation correctly excluded same-repository overlap but still
had a scheduler-level head-of-line defect. With mailbox order `A1, A2, B1`, the
watcher could encounter blocked `A2`, wait for `A1`, and therefore fail to discover
independent `B1` in time to overlap it.

The release candidate replaces that blocking admission path with a bounded
watcher-owned pending set. Busy-repository jobs wait there while discovery
continues within the configured bound. Runnable repositories are selected by a
round-robin cursor, while the first queued request for each repository remains the
only eligible request from that repository. Canonical-path exclusion is rechecked
from the latest registry immediately before worker handoff.

A deterministic regression test forces the adverse `A1, A2, B1` ordering and
requires `B1` to start before `A2`, while independently asserting that `A2` cannot
start before `A1` releases repository ownership.

## Post-RC1 production-canary finding fixed in RC2

The first live post-promotion canary found a second, distinct watcher-level defect
that same-batch tests could not expose. The concurrent path accepted all requests
visible in one mailbox list, dispatched them correctly, and then synchronously
drained that batch before returning to the outer watcher loop. A request uploaded
while a long worker was already executing therefore remained remote and undiscovered
until that worker finished, leaving otherwise idle worker capacity unused. In
practice, concurrency depended on requests being visible in the same list call.

RC2 keeps transport ownership on the watcher but re-polls the transaction mailbox
while repository workers execute. Newly arrived requests are validated and admitted
against the same bounded pending set; active and pending filenames are excluded from
rediscovery; durable-result recovery still precedes any re-execution; and canonical
repository exclusion is rechecked immediately before handoff. A non-blocking worker
completion API lets the watcher alternate completion reaping with mailbox polling
without transferring Drive/rclone ownership to worker threads.

The new regression starts with only repository A1 visible. After A1 has begun, A2
and independent B1 are inserted into a later mailbox poll. The test requires B1 to
start on the idle second worker while A1 is still active, and simultaneously proves
that A2 cannot start until A1 releases the repository. This is the exact arrival
pattern that failed in the production canary.

## Concurrency / recovery attack matrix

| Scenario | Release-candidate result |
| --- | --- |
| different repositories overlap | deterministic barrier test passes |
| same repository overlaps | rejected by canonical-path in-flight ownership |
| two registry IDs point to same path | alias test serialises them by resolved path |
| same-repository burst hides independent work | fixed; adverse ordering regression passes |
| independent request arrives after a long job starts | fixed in RC2; live-arrival later-poll regression passes |
| worker A raises while worker B succeeds | B result remains correctly mapped/successful |
| completions arrive out of submission order | per-handle result mapping remains exact |
| post-durable publication fails while other work is active | completed result stays durable; pending/active work is preserved and recovery republishes without re-execution; pre-durable persistence failure still reaps fail-closed |
| daemon/scheduler restart after publication outage | fresh scheduler republishes durable results without local re-execution |
| command-log retention during execution | active transaction IDs are excluded from pruning |
| materialisation during edit work | watcher drains local transaction work first; materialisation is a quiescent barrier |
| control request during long edit | doctor/diagnostics remain watcher fast-path operations |
| cancellation / shutdown | one Event reaches all workers; non-daemon workers are joined explicitly |
| mailbox flood | bounded validated backlog and fixed worker count prevent unbounded local scheduler growth |
| 10 repositories / 4 workers | bounded-scale soak passes with zero same-repository overlap |

The pre-concurrency A-phase crash matrix remains authoritative for the local Git
transaction boundaries: worktree creation, patch/stage, configured commands,
commit identity, push, local result persistence, result upload, marker creation,
request deletion, and retry/recovery. The concurrent layer does not replace those
durable facts with scheduler memory.

## Resource / denial-of-service audit

Remote writers cannot select worker count, queue size, command argv, transport
parallelism, or process count. The operator controls fixed worker/backlog limits.
Each request remains subject to the protocol's size/patch/command/snapshot/output
bounds before or during execution. When the validated local backlog reaches its
configured bound, the watcher drains work before downloading another untrusted
request.

The scheduler does not allocate one thread per request. Worker threads are created
once at daemon start. Repository aliases cannot create additional mutation slots
for the same canonical path. Transaction IDs are reserved while active. Existing
A/J protections continue to cover duplicate IDs/filenames, malformed JSON,
result replay/forgery, path/symlink/submodule changes, protected automation paths,
command-output amplification, snapshot filtering/limits, and watch-lock ownership.

No unresolved high- or critical-severity concurrency/resource finding remains.

## Transport decision (Phase F)

Mailbox transport stays serial and watcher-owned. The normal persistent-RC
measurements are small compared with repository validation. The first C1 Mac gate
showed a one-off 8.012 s subprocess-fallback directory listing beside a 74.137 s
configured test command and 80.244 s local transaction; that is a transport-health
outlier, not evidence that concurrent Drive mutation is worth the uncertain-write
and shared-client race surface. Parallel rclone transport is therefore rejected for
this release.

## Adaptive scheduling decision (Phase I)

Adaptive worker counts are rejected. Configured repository commands may already
spawn parallel builds, so CPU count is not a reliable safe capacity signal. No
benchmark demonstrates enough benefit to justify adding feedback/control state to
the correctness boundary. Fixed validated limits are retained.

## Observability gate

Watcher-owned metrics now expose queue depth, active workers, active repositories,
queue wait, observed execution interval, and worker utilisation. Concurrent
`diagnostics` additionally reports the live scheduler/backlog limits and counts.
These fields contain no repository filesystem paths. Metrics remain bounded and
best-effort; transaction correctness never depends on them.

## Release gates

Before the candidate may be called `1.0.0rc4`:

1. complete suite passes under bounded default sharding;
2. complete suite passes under an independent hash seed and an alternate bounded shard count;
3. deterministic concurrency/recovery/fairness/scale regressions pass;
4. the external-process canary proves two-worker makespan is materially below the
   one-worker sum on the qualification host;
5. compile, shell-syntax, and `git diff --check` gates pass;
6. the exact cumulative patch is exercised by the production Mac through the
   bridge's configured `test` command;
7. the pushed candidate is independently materialised from Drive and every tracked
   file hash is compared with the exact local candidate tree;
8. no high/critical audit finding remains unexplained.

The release candidate does not require parallel transport or adaptive scheduling;
those are explicit rejected designs, not unfinished implementation.

## RC2 production live-canary finding and RC3 hardening

The RC2 production canary exposed a second watcher-level failure mode after the original live-arrival repair. A later same-repository request could be discovered and retained while A1 was active, but a subsequent transient mailbox-list failure caused the concurrent processing frame to unwind. The exception cleanup path cleared the validated pending queue and then blocked while reaping active workers. Any independent repository request arriving during that interval could not be discovered until the active worker finished and the outer watch loop restarted. This recreated head-of-line blocking under a transport hiccup even though the steady-state scheduler was correct.

RC3 changes the live-repoll failure boundary: `BridgeError` during active live discovery is recorded as `scheduler-live-poll-error`, already validated pending work and active workers are preserved, and discovery is retried on the next live-poll interval. Persistent transport failure is still surfaced by the ordinary outer poll after the active batch drains, so the change does not convert a broken mailbox into silent success. Scheduler enqueue/dispatch/finish metrics now include the bounded public `transaction_id`, allowing production canaries to prove ordering directly rather than inferring job identity from execution duration.

A dedicated regression injects A1, later A2 on the same repository, then a transient live-list failure before later B1 on an independent repository. RC3 must retain A2, retry discovery, dispatch B1 before A2, prevent A2 from starting before A1 releases, emit the live-poll-error metric, and attach transaction identity to scheduler lifecycle metrics.


## RC3 production result-publication finding and RC4 hardening

The post-promotion RC3 canary proved the live-repoll repair under real transient list failures, but its transaction-ID chronology exposed a separate watcher-owned transport boundary. Independent B1 finished while long-running A1 was active, yet B1's first remote result-publication attempt failed. The signed local result had already been persisted, but the publication `BridgeError` escaped the concurrent processing frame. The outer fail-closed reaper then cleared queued same-repository A2 and synchronously waited for A1. A2 was rediscovered only after A1 completed. This preserved correctness and idempotency, but recreated avoidable head-of-line blocking under a result-publication hiccup. RC3 is therefore not tagged.

RC4 makes the durable-result boundary explicit in the concurrent completion path. The watcher builds and persists the signed local acknowledgement first. Only a `BridgeError` from the subsequent remote publication/cleanup phase is contained as `scheduler-publication-error`; the repository worker is released, validated pending work is preserved, unrelated workers continue, and the existing durable-result recovery path republishes without re-executing the mutation. Failures before the signed result is durably persisted still propagate to the outer fail-closed reaper.

Dedicated regressions prove both sides of the boundary: a B1 publication failure while A1 is active must not discard queued A2 or drain A1, and later recovery must publish B1 without mutation re-execution; conversely, a durable-local-result persistence failure must still unwind and reap all in-flight workers.

## RC5 automatic-discovery adversarial extension

RC5 extends the audit surface from scheduler concurrency to repository-registry lifecycle. Required regressions now prove:

- a newly created valid Git repository beneath an approved root is discovered and path-free-published without manual `scan`;
- discovery continues while a long unrelated worker is active;
- unchanged membership causes no remote registry write;
- fake `.git` markers are rejected by Git validation;
- a temporarily unavailable approved root cannot cause mass deregistration;
- repeated unknown-repository requests cannot bypass the configured scan interval;
- registry-publication failure leaves the previous local registry untouched;
- diagnostics expose bounded discovery state without filesystem paths;
- moved intact repositories retain their ID where safely identifiable;
- a different repository replacing a path retires the old policy ID, and full scans never recycle retired IDs;
- local marker/history identity and the retired-ID ledger never enter public `repos.json`;
- repository identity is checked before command/push policy is frozen for worker handoff.

The initial inode-only replacement idea was deliberately rejected during adversarial testing because a filesystem may immediately reuse a deleted `.git` inode. The accepted design additionally binds the policy ID to Git history. This favors fail-closed policy reset over accidental privilege inheritance.

RC5 also broadens the RC4 post-durable publication boundary only for expected local filesystem `OSError` conditions during outbox staging, publication-marker persistence, or local acknowledgement cleanup. Those errors are normalised into the already-contained `BridgeError` recovery path after the signed local result is durable. It does not catch arbitrary exceptions and does not alter pre-durable failure semantics.
