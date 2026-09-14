# Concurrency and multiple clients

`llm-git-bridge` `0.3.1` keeps the frozen `0.3.0` release as its semantic oracle. The current C1 development branch adds **feature-gated bounded local concurrency across different canonical repositories** while retaining one mailbox owner. `max_workers` still defaults to `1`; the two-worker mode remains experimental until the production canary and post-canary audit pass.

## What is concurrent

Several clients can independently upload request objects to the mailbox. They do not need to share a process or ChatGPT conversation.

This is suitable for:

- multiple ChatGPT tabs/sessions;
- different LLM providers using the same mailbox protocol;
- independent work on different repositories;
- independent branches in the same repository.

## What is serialised

Only one local watcher may hold the mailbox lock. Drive/rclone request download, signed-result publication, request deletion, metrics, and reconciliation stay serial and watcher-owned. With `max_workers > 1` on the C1 development branch, local transaction execution may overlap only when the resolved repository paths differ. The scheduler permits at most one in-flight transaction per canonical repository; materialisation remains a watcher-side quiescent barrier.

## Queue ordering is not FIFO

Do **not** use request creation time as a dependency mechanism. The transport adapter sorts the filenames it sees lexicographically, not by creation time. Requests can also be deferred and retried after transient transport failures, so effective execution order is not submission order and should not be treated as a dependency mechanism.

A five-request production probe on 13 September 2026 completed all five requests successfully but processed them in this order after one request encountered transient download retries:

```text
2 -> 3 -> 1 -> 4 -> 5
```

If request B depends on request A, wait for A's signed success result and construct B against the new known commit. Do not submit both and rely on upload order.

## Transaction IDs are global mailbox identities

Use a distinct transaction ID for every independent request. A useful pattern is:

```text
tx-<purpose>-<date>-<random-or-session-token>
```

Transaction IDs are also used as local state/worktree/log identities. Exact retry/recovery of the same request is supported after crashes, but intentional reuse for unrelated work is incorrect. Google Drive permits duplicate filenames, so two clients should never intentionally create different requests with the same transaction filename.

## Working on the same repository

### Different new branches from the same base

This is supported. Two requests can target different new safe branches with the same `base_sha`, provided the authoritative checkout HEAD has not advanced before each new branch is created.

### The same branch

Treat a bridge branch as a serial history. Once one transaction advances it, a later transaction that still names the old base is rejected as stale. The bridge does not auto-rebase, auto-merge, or resolve conflicts.

### The authoritative checkout changes

For a brand-new branch, the requested base must still equal the authoritative repository HEAD. A human/local change that advances HEAD can therefore make queued requests stale. This is intentional.

## Head-of-line blocking

With the default `max_workers=1`, one slow transaction still delays later edit transactions. In the C1 development mode with `max_workers=2`, a slow transaction for repository A can overlap local execution for repository B, while same-repository work remains serialized. Mailbox transport and result publication are still serial.

The A3 precursor once completed the configured validation gate in 50.4 seconds, but A4 deliberately established a repeated-run gold baseline instead: the hardened 196-test tree completed three production-Mac runs in 67.3–71.2 seconds (median 68.0 s). A transaction running that suite therefore still occupies the single execution stream for roughly a minute before the next edit transaction can start.

Lightweight `doctor`/diagnostics requests are much faster but still wait behind an already-running edit transaction.

## Recommended multi-session workflow

1. Give every session its own transaction-ID namespace and branch name.
2. Materialise or read the current repository state before preparing an edit.
3. Use separate branches for independent work.
4. Wait for a signed result before preparing work that depends on a prior request.
5. Expect stale-base errors when a local human or another session has advanced the relevant history.
6. Do not run a second local watcher; the singleton lock is a safety property.

## Future scaling

C1 now implements the first bounded two-worker step using scheduler-owned canonical-repository exclusion and watcher-owned transport. It remains feature-gated until deterministic C2 tests and the C3 production canary establish correctness and host-level throughput.
