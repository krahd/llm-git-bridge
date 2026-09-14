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
