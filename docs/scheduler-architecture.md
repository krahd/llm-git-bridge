# Scheduler architecture

This document records the staged execution boundary introduced after the v0.3.0
single-thread gold baseline and the worker-lifecycle decision that Phase B2 must
follow. It is deliberately narrower than the eventual multi-repository scheduler:
Phase B1 changes structure, not execution concurrency.

## Gold semantic oracle

The v0.3.0 single-thread oracle is branch
`ai/single-thread-gold-v0-3-0-20260913` at
`dfe16d0e57c0b33b1d5fcf10b2bdc919fde36442`. Scheduler work must preserve its
request identity, replay, stale-base, Git transaction, result authentication,
publication, cleanup, and recovery semantics before worker count can exceed one.

## Phase B1 stage boundary

One mailbox poll is now expressed as explicit synchronous stages:

1. **poll** — list valid transaction entries once and freeze their names/sizes;
2. **classify** — separate executable requests from valid acknowledged requests,
   repairing malformed local markers only from authenticated remote results;
3. **recover** — republish durable local results before any request may re-execute;
4. **download** — obtain exact request text or defer on transient transport error;
5. **validate** — parse strict JSON and validate filename/request identity;
6. **execute** — dispatch one validated request synchronously, refreshing registry
   state immediately before repository-dependent work;
7. **sign and persist** — bind the result to exact request bytes, authenticate it,
   and save the durable local result before remote publication;
8. **publish and clean up** — upload the signed result, persist the publication
   marker, delete the request, and retire transient local state.

The poll, downloaded-request, and validated-request handoff objects are frozen
records. The request payload dictionary itself is treated as read-only by the
scheduler boundary; execution functions retain the existing protocol API.

No worker thread is introduced in Phase B1. `process_pending_once()` remains one
synchronous execution stream and is expected to remain semantically equivalent to
v0.3.0.

## Phase B2 worker-lifecycle decision

The first worker implementation will use explicit long-lived, **non-daemon**
`threading.Thread` workers fed by a bounded `queue.Queue`, initially with exactly
one worker.

The decision is based on Python 3.14 primary documentation:

- `queue.Queue` supplies the synchronization/locking required for safe producer /
  consumer handoff, and `task_done()` / `join()` provide an explicit completion
  invariant. See <https://docs.python.org/3.11/library/queue.html>.
- Python documents that daemon threads are abruptly stopped during interpreter
  shutdown and recommends non-daemon threads plus a signalling mechanism such as
  `threading.Event` when resources must be released cleanly. See
  <https://docs.python.org/3.11/library/threading.html>.
- `ThreadPoolExecutor` is a convenient high-level API, but Python explicitly says
  it is not recommended for long-running tasks because all executor threads are
  joined by an interpreter-exit handler that runs before normal `atexit` handlers.
  That lifecycle is a poor fit for this long-lived daemon. See
  <https://docs.python.org/3.11/library/concurrent.futures.html>.

The project supports Python 3.11+, so B2 cannot depend on the newer
`Queue.shutdown()` API. It will use a private sentinel plus a `threading.Event`,
`task_done()` / `join()`, and explicit non-daemon thread joins so the same lifecycle
works on the minimum supported Python. Therefore B2 will not use
`ThreadPoolExecutor` as the daemon's primary worker lifecycle. An executor wrapper
may be reconsidered only if later evidence shows a material benefit without
weakening shutdown/recovery reasoning.

## Ownership rule for the first scheduler

The watcher/main thread remains the sole owner of Drive/rclone mailbox I/O,
result publication, request deletion, startup reconciliation, and scheduler state.
Workers will eventually receive immutable local-execution jobs and return immutable
outcomes to the owner. This keeps transport serialization unchanged while the local
repository execution path is proven independently.

At `max_workers=1`, B2 must match the gold oracle before any two-repository
parallelism is enabled.

## Phase B2 one-worker implementation

Phase B2 instantiates the explicit scheduler in the daemon with exactly one active
worker and a queue capacity of one. The queue is intentionally bounded even though
the watcher still waits synchronously for each completion: the same invariant will
remain when C1 begins allowing more than one repository job in flight.

The B2 ownership split is deliberately conservative:

- the **watcher/main thread** owns mailbox listing/download, registry refresh,
  control requests, materialisation requests, result signing/persistence,
  result/snapshot publication, remote request deletion, metrics, and maintenance;
- the **worker thread** receives an immutable transaction specification containing
  exact request text, canonical repository identity/path, a frozen JSON copy of
  locally configured commands, and commit/push policy;
- the worker performs only local Git/worktree/configured-command execution and
  returns a local transaction outcome over a per-task queue;
- optional transaction snapshot upload is performed by the watcher after the
  worker returns, so the rclone transport object never crosses the B2 worker
  boundary.

Control requests and materialisation requests therefore have an explicit B2 policy:
they remain watcher-owned and bypass the transaction worker queue. Materialisation
contains both local repository work and remote snapshot publication; C1 must split
those portions before materialisation can participate safely in cross-repository
parallelism.

The scheduler uses non-daemon `threading.Thread` workers, a bounded `queue.Queue`,
a private sentinel, and a `threading.Event` cancellation flag. `task_done()` /
`join()` plus explicit thread `join()` form the shutdown invariant. Public scheduler
operations are owner-thread-only; worker errors are returned through the completion
channel and do not silently kill the worker loop.

The daemon signal handler only sets cancellation state. A running local transaction
observes that state through the existing configured-command cancellation path; the
watcher remains responsible for publishing the resulting durable terminal result
before scheduler shutdown joins the worker.

## Phase B3 ownership and thread-safety audit

Before `max_workers` may exceed one, every mutable component has an explicit
owner or serialization rule. The rules below are part of the scheduler contract,
not implementation suggestions.

| Component | B3 ownership policy | Concurrent-phase invariant |
| --- | --- | --- |
| Drive/rclone transport object and RC process/socket state | **watcher-owned only** | Workers never receive transport objects or call mailbox APIs. |
| Remote inbox/result listing, request download/delete, result/snapshot upload | **watcher-owned only** | All remote mailbox side effects remain serialized by the watcher. |
| `registry.json` and registry publication | **watcher-owned writer; workers receive immutable resolved snapshots** | Registry is re-read immediately before repository-dependent dispatch. A queued/running job never follows a later mutable registry object. |
| Loaded configuration | **watcher-owned; immutable values copied into each worker task** | Worker handoff contains only canonical repo/path plus JSON-frozen command configuration and boolean/string policy. |
| Scheduler queue and task IDs | **scheduler-owned; `queue.Queue` supplies handoff synchronization** | Public submit/wait/shutdown operations are owner-thread-only. |
| Cancellation state | **shared only through `threading.Event`** | Signal handler/owner may set it; workers only read it. No unsynchronized boolean is shared with workers. |
| Per-task completion | **single-producer worker / single-consumer watcher via `queue.Queue`** | A worker publishes exactly one outcome or captured exception. |
| Future repository in-flight set | **scheduler-owned only** | C1 must claim a canonical repository key before dispatch and release it only after local completion; at most one mutating job per repo. |
| Git refs, index/worktree metadata, commits, pushes | **worker-local execution under scheduler repository ownership** | Different repositories may run independently; the scheduler must serialize all mutating work for the same canonical repository. Existing CAS/stale-base checks remain a second fail-closed layer. |
| `STATE_DIR/worktrees/<txid>` | **worker-local, transaction-scoped** | Transaction IDs are globally bound to request filenames; owner cleanup occurs only after that task completes. |
| `STATE_DIR/transactions/<txid>` claim/patch state | **worker-local while executing; watcher cleanup after completion/publication** | No watcher deletion while the txid is in flight. |
| `STATE_DIR/command-runs/<txid>` and `command-homes/<txid>` | **worker-local while executing; watcher cleanup after completion** | Paths are txid-disjoint. |
| `STATE_DIR/command-logs/<txid>` | **worker-local writer; watcher retention maintenance only when the txid is not in flight** | C1 must treat active txids as protected from pruning, or run command-log pruning only at a scheduler quiescent point. |
| Durable local results `STATE_DIR/results/*.json` | **watcher-owned only** | Worker returns an unsigned local outcome; watcher signs/persists it before any remote acknowledgement. |
| Publication markers `published-results/` and corrupt-marker quarantine | **watcher-owned only** | Replay/acknowledgement state never crosses the worker boundary. |
| Inbox/outbox/reconcile/corrupt-result staging | **watcher-owned only**, except worker-local transaction files listed above | Startup reconciliation and remote-result recovery run on the watcher before/around scheduling, never inside workers. |
| Metrics JSONL and diagnostics reads | **watcher-owned writer/reader** | Workers return timings inside outcomes; watcher appends metrics. This avoids concurrent rotation/appends. |
| Local result pruning | **watcher-owned only** | It operates only on watcher-owned durable results/markers and is independent of in-flight worker scratch state. |
| Command-log pruning | **watcher-owned maintenance with in-flight exclusion** | This is the one retention operation that can overlap worker-owned paths in C1; C1 must explicitly supply/protect in-flight txids before enabling >1 worker. |
| Materialisation worktrees and snapshot publication | **watcher-owned in B3** | Materialize remains outside the transaction worker pool. C1 must split local materialisation from transport publication before making it concurrent. |
| Watch lock and OS signal handlers | **watcher/main-thread only** | Python signal handling remains on the main thread; shutdown sets the Event and joins all non-daemon workers before releasing the lock. |

### Audit conclusions

The B2 implementation already satisfies the table at `max_workers=1`: the worker
boundary contains no transport, registry object, result marker, metric writer, or
other watcher-owned mutable object. Repository-local state is transaction-scoped
or protected by the still-single execution stream.

Two constraints are intentionally carried forward as **C1 preconditions**, rather
than hidden assumptions:

1. the scheduler must own a canonical per-repository in-flight set before a second
   worker is enabled; and
2. command-log retention must exclude in-flight transaction IDs (or execute only
   at a quiescent point) before multiple workers can overlap.

Materialize is deliberately watcher-owned through B3. It cannot simply be placed
on the transaction worker queue because its current implementation combines local
Git work with remote snapshot publication. C1 must either leave it serial or split
those responsibilities first.

No broad process-wide lock is introduced. The intended concurrent model remains
ownership plus message passing: watcher owns transport/durability/publication,
scheduler owns admission and repo exclusion, and each worker owns one local job.

## Phase C1 two-worker implementation

C1 enables bounded local transaction concurrency without changing mailbox
ownership. `max_workers` is validated in the range 1..8 and remains **1 by
default** while the production canary is still pending. When configured above
one, only local transaction execution enters the concurrent path.

The scheduler now owns two admission identities for every submitted task:

- a globally unique transaction ID; and
- a **canonical repository scheduling key**, derived from the resolved local
  repository path rather than the public registry ID.

The path-based key is deliberate: two registry aliases that resolve to the same
repository must not bypass same-repository exclusion. At most one transaction for
one canonical repository may be in flight. Different canonical repository paths
may overlap up to `max_workers`.

Transport remains watcher-only. Downloads occur serially, workers return local
outcomes, and snapshot/result publication plus request deletion remain serialized
on the watcher. `doctor` and diagnostics stay on the watcher fast path.
Materialisation remains watcher-owned and is preceded by a quiescent worker
barrier; it is not parallelised in C1.

Command-log retention receives the current set of in-flight transaction IDs so
worker-owned logs cannot be pruned. If result publication fails while other jobs
are still running, the watcher reaps every submitted worker and durably persists
each completed signed result before propagating the original publication error.
This prevents an unreachable handle from leaving a repository permanently marked
in flight after a transient transport failure.


## Phases C2–K: fairness, recovery, observability, and release policy

The post-C1 adversarial pass found a watcher-level head-of-line flaw rather than a worker-lock flaw: when the inbox yielded `A1, A2, B1`, C1 could block waiting for `A1` merely because `A2` was encountered before independent `B1`. The corrected watcher never waits solely because the next validated transaction belongs to a busy repository. It retains the request in a bounded watcher-owned pending set, continues discovery while capacity permits, and dispatches the first runnable request selected by repository round-robin. Per-repository discovery order is retained; global FIFO is intentionally not promised.

Backpressure is explicit. `max_pending_jobs` defaults to 8, is validated from 1 through 32, and cannot be lower than `max_workers`. The bound counts both queued and in-flight validated jobs before another remote request is downloaded. Together with the protocol request-size limit this prevents mailbox writers from turning the scheduler into an unbounded memory queue. The operator command `configure-concurrency` changes both limits atomically through normal config validation.

Scheduler observability remains watcher-owned. Enqueue/dispatch/finish events record bounded queue depth, active workers, active repositories, queue wait, execution timing and utilisation. Concurrent diagnostics include the live worker/backlog limits and counts. No repository path or worker-local mutable object is exposed.

Crash consistency remains durability-first. Worker-local execution returns an outcome to the watcher; the watcher signs and persists the result before remote publication. A publication exception reaps all submitted workers and leaves their durable results authoritative. On restart, durable-result reconciliation runs before request execution and republishes those acknowledgements; the scheduler's lost in-memory queue is not a recovery input.

Transport I/O remains serial by design. The watcher is the sole rclone/Drive owner even with multiple local workers. The programme found no ordinary end-to-end gain large enough to justify sharing transport state or increasing uncertain-write concurrency. Likewise, adaptive concurrency was rejected: explicit fixed limits are easier to audit and avoid guessing host capacity when configured repository commands may already spawn parallel builds.

The release candidate therefore has one simple concurrency rule: parallelise only independent local repository work, keep every global/durable side effect under one owner, and bound every queue/worker dimension explicitly.
