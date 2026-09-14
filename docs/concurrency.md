# Concurrency and multiple clients

`llm-git-bridge` `1.0.0rc2` keeps the frozen `0.3.0` release as its semantic oracle and adds **bounded local concurrency across different canonical repositories** while retaining one mailbox owner. `max_workers` still defaults to `1` as a conservative migration policy; operators can explicitly enable the recommended initial setting of two workers after qualification on their host.

## What is concurrent

Several clients can independently upload request objects to the mailbox. They do not need to share a process or ChatGPT conversation.

This is suitable for:

- multiple ChatGPT tabs/sessions;
- different LLM providers using the same mailbox protocol;
- independent work on different repositories;
- independent branches in the same repository.

## What is serialised

Only one local watcher may hold the mailbox lock. Drive/rclone request download, signed-result publication, request deletion, metrics, and reconciliation stay serial and watcher-owned. With `max_workers > 1`, local transaction execution may overlap only when the resolved repository paths differ. The scheduler permits at most one in-flight transaction per canonical repository; materialisation remains a watcher-side quiescent barrier.

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

With the default `max_workers=1`, one slow transaction still delays later edit transactions. With `max_workers=2`, a slow transaction for repository A can overlap local execution for repository B, while same-repository work remains serialised. The watcher continues polling the mailbox while workers run, so repository B does not have to be present in the same initial list call as repository A. Mailbox transport and result publication remain serial and watcher-owned. A bounded watcher-owned backlog (`max_pending_jobs`, default 8, valid 1–32 and never lower than `max_workers`) allows the watcher to look past blocked same-repository work without accepting an unbounded amount of untrusted request data. Repository admission is round-robin across runnable repositories while preserving per-repository discovery order.

The A3 precursor once completed the configured validation gate in 50.4 seconds, but A4 deliberately established a repeated-run gold baseline instead: the hardened 196-test tree completed three production-Mac runs in 67.3–71.2 seconds (median 68.0 s). A transaction running that suite therefore still occupies the single execution stream for roughly a minute before the next edit transaction can start.

Lightweight `doctor`/diagnostics requests are discovered on watcher re-polls and remain watcher-owned fast-path operations; they are not forced to wait for an already-running repository worker to finish.

## Recommended multi-session workflow

1. Give every session its own transaction-ID namespace and branch name.
2. Materialise or read the current repository state before preparing an edit.
3. Use separate branches for independent work.
4. Wait for a signed result before preparing work that depends on a prior request.
5. Expect stale-base errors when a local human or another session has advanced the relevant history.
6. Do not run a second local watcher; the singleton lock is a safety property.

## Future scaling

The release candidate has completed the deterministic concurrency, fairness, recovery, scale, and security test campaigns. Serial mailbox transport is intentional: workers never share the rclone transport object, and observed edit latency is dominated by local validation rather than ordinary RC mailbox operations. Adaptive worker counts were rejected in favour of explicit fixed limits because no evidence showed that a more complex policy improves correctness or predictable host load.

Enable two workers with:

```bash
bin/llm-git-bridge configure-concurrency --workers 2 --max-pending-jobs 8
bin/llm-git-bridge daemon restart
```

`status` reports the configured worker/backlog limits. `diagnostics` results include live scheduler counts when processed by the concurrent path, and scheduler metrics expose queue depth, active workers/repositories, queue wait, execution timing, and utilisation without exposing local paths.
