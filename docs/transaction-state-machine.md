# Transaction state machine

This document defines the correctness model of the single-worker bridge. Later
scheduler/concurrency implementations must preserve these semantics when run with
one worker and must not weaken the durable/replay boundaries described here.

## Trust and durability model

The bridge has four relevant state domains:

1. **Remote request inbox** — `v2/transactions/<transaction_id>.json` is the
   trigger. A request remains pending until an authenticated result has been
   published and cleanup has been attempted.
2. **Local runtime state** — downloaded request bytes, transaction staging,
   worktrees, command workspaces/logs, and locally durable result JSON.
3. **Remote result archive** — `v2/results/<transaction_id>.json` is an
   HMAC-authenticated acknowledgement. The filename alone is not trusted.
4. **Git repository state** — bridge commits, safe target refs, and persistent
   transaction-identity claim refs. Git state is authoritative for recovery
   after a commit has been published.

The result-authentication key lives in persistent bridge configuration rather
than disposable runtime state, so authenticated remote results remain verifiable
after runtime-state loss.

The bridge assumes the local account/configuration is trusted. Remote mailbox
writers are not trusted to create authoritative results merely by writing a file
with a plausible name.

## Mailbox request lifecycle

The normal request path is:

```text
remote request
  -> list / select candidate
  -> bounded download
  -> exact request-byte SHA-256
  -> strict JSON parse + identity validation
  -> local operation
  -> signed result object
  -> atomic local result save
  -> remote result upload
  -> local publication marker
  -> remote request deletion attempt
  -> local request/workspace/outbox cleanup
  -> bounded local-result / command-log pruning
```

### Replay authorities

Before executing a candidate request, the bridge evaluates, in order:

- a valid local publication marker;
- a locally durable signed result, reconciled against any existing remote
  result before a second upload is attempted;
- at watcher startup, an authenticated remote result for a still-pending
  request;
- for Git transactions, persistent Git transaction identity and bridge commit
  metadata.

A malformed local result or publication marker is quarantined rather than
trusted. A forged/unsigned remote result is never promoted into replay state.

### Crash boundaries

| Crash/failure point | Recovery behaviour |
| --- | --- |
| before request download completes | request remains remote; retry download |
| after download, before local operation | request remains remote; re-enter validation/operation |
| after local operation, before local result save | non-Git control requests may repeat; Git mutation is recovered from Git identity/commit state |
| after local result save, before remote result visibility | wait a short ambiguity grace period, check remote first, then republish local signed result |
| remote upload accepted but caller times out | authenticate existing remote result; do not blindly issue a second write |
| after remote result, before marker | authenticate remote result and reconstruct marker |
| after marker, before request/local cleanup | do not re-execute; next poll retries remote cleanup and retires local staging |
| request deletion fails | marker suppresses re-execution; deletion is retried later |
| malformed marker/local result | quarantine; recover from authenticated remote/Git state or re-enter ordinary processing |

Publication markers are replay-suppression state, not proof by themselves that
an arbitrary remote result is genuine. Markers created through reconciliation
are derived only from authenticated remote result objects.

## Git transaction lifecycle

For `kind=transaction`, the single-worker semantic path is:

```text
validate request
  -> verify authoritative checkout has no tracked/index modifications
  -> verify persistent transaction-ID claim, if any
  -> inspect exact direct target branch ref
  -> validate declared base
  -> atomically create transaction-ID claim if absent
  -> retire stale disposable worktree
  -> create disposable worktree
  -> apply patch to index + worktree
  -> git diff --check
  -> validate changed paths and Git modes
  -> record staged tree
  -> prepare isolated command workspace (only if commands requested)
  -> run configured commands with timeout/cancellation/process-group cleanup
  -> re-check Git/control invariants after every command
  -> create commit object with reserved transaction/request-hash trailers
  -> verify raw commit object, parent, tree, and bridge identity
  -> compare-and-swap publish exact target ref with git update-ref --no-deref
  -> optional non-force push of that exact safe ref
  -> optional snapshot of the exact durable commit
  -> remove disposable worktree/workspace
  -> return success result
```

### Existing versus new target branches

For an existing target branch, the disposable worktree is attached to the
branch. Git therefore retains its native protection against checking out/moving
a branch that is already checked out elsewhere. Publication still uses an
old-OID compare-and-swap so an external ref move cannot be overwritten.

For a new target branch, work starts detached at the declared base. The branch
is not created until the commit object has passed all verification. Publication
uses an empty expected old OID, so creation fails if another actor created the
ref first.

All refs used as mutation boundaries are first read as **exact direct refs**.
Symbolic refs are rejected. Publication/claim creation uses `update-ref
--no-deref` so a race cannot redirect a safe-looking ref into another namespace.

### Git crash recovery

A request-specific SHA-256 is embedded in both a persistent claim ref and the
bridge commit trailers. On retry:

- a conflicting transaction-ID claim is rejected;
- an existing branch at the declared base proceeds normally;
- an existing branch whose tip is the exact verified bridge commit for the same
  transaction/request/base is treated as a recovered durable commit;
- any other branch tip is a stale/conflicting base and is rejected.

Recovery reads raw commit objects with replacement/graft effects disabled. It
must not accept a commit merely because its message contains bridge-like text.

Crash points before target-ref publication can leave only disposable worktree
metadata or unreachable Git objects; retry retires stale worktree state and
re-applies from the declared base. A crash after target-ref publication must not
create a second bridge commit for the same request.

## Push and snapshot semantics

The local commit is the primary transaction result. Push and snapshot
publication happen after that durable local mutation and are secondary:

- push is opt-in per repository and never force-pushes;
- push failure/timeout does not erase or misreport the durable local commit;
- if local policy later disables push during crash recovery, recovery remains a
  successful local commit with a secondary push error;
- snapshot generation/publication failure is reported as a secondary error;
- branch materialisation pins the branch OID first and snapshots a detached
  worktree at that exact OID.

## Command lifecycle

Configured commands run from a copied, tracked-file-only workspace without Git
metadata or obvious secret material. They receive a scrubbed environment, have
bounded output, and run in a dedicated process group.

Timeout or daemon-stop cancellation retires the process group and causes the
transaction to fail before commit publication. After every successful command,
the bridge verifies that the transaction worktree's staged tree, HEAD/ref state,
and protected Git control state have not changed.

## Cleanup and retention

Per-request inbox/workspace/outbox staging is disposable after a valid
publication marker exists and is retired both on the normal path and on the
next idle poll after a crash at the marker/cleanup boundary. Command logs and
local result cache are bounded.

Publication markers and Git transaction-identity claim refs are deliberately
long-lived replay-protection records. Their growth is therefore a performance /
compaction concern rather than something that may be pruned without an explicit
replacement replay ledger. Phase A3 should measure their scaling cost before
changing that invariant.

## Single-worker ordering

The current watcher is the sole mailbox consumer and executes candidate requests
serially. This ordering is an implementation property of the gold baseline, not
a requirement that unrelated repositories remain globally serial forever.
Future schedulers may execute different repositories concurrently only if they
preserve all per-request durability, per-repository mutation exclusion, replay,
and fail-closed conflict semantics above.
