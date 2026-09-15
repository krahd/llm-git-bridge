# Protocol workflow reference

## Mailbox layout

```text
v2/meta/repos.json
v2/repos/<repo-id>/snapshot.json
v2/repos/<repo-id>/branches/<branch-token>/snapshot.json
v2/transactions/<transaction-id>.json
v2/results/<transaction-id>.json
```

The repository index is path-free. Local repository paths are intentionally not exposed.

## Client loop

### 1. Discover

Read `v2/meta/repos.json`. Select by stable repository ID whenever possible; use a name only when it is unambiguous.

### 2. Refresh authoritative state

Submit a materialisation request. For the authoritative current checkout, omit `branch`. For an existing safe-prefix bridge branch, specify that branch.

Wait for the materialisation result, then read the referenced snapshot. Use its exact HEAD and file hashes/content as the basis for edits.

### 3. Prepare the patch

Create a standard unified Git diff against the materialised base. Do not include `.git` state or local paths. Avoid protected content and CI/automation control paths. The bridge will independently validate changed paths and Git modes.

### 4. Select validation commands

`run` contains symbolic command names only. The corresponding argv is configured locally by the operator. A request may not supply or alter the argv.

If the required symbolic command is not configured, do not emulate it with shell text in the request. Ask the operator to configure an appropriate command or proceed only if the task is allowed without it.

### 5. Submit

Use a unique safe transaction ID, and make the filename exactly `<transaction_id>.json`. Google Drive may permit duplicate filenames, so uniqueness is a client responsibility.

For changed request content, always use a new transaction ID. Reuse is reserved for exact retry/recovery of identical request bytes.

### 6. Observe

The inbox file may remain visible during long tests. That is normal. If operational visibility is needed, submit a separate diagnostics request; do not infer failure from inbox presence.

### 7. Consume result

Read `v2/results/<transaction_id>.json`. A terminal result has `status: success` or `status: error`.

For edits, distinguish:

- durable local commit identity;
- validation command return codes/durations;
- push status;
- snapshot-deferred/snapshot-path/snapshot-error state.

Do not treat a secondary push or snapshot error as evidence that the already-created local commit vanished.

### 8. Chain dependencies

If a later edit depends on the result, wait first. Materialise the returned branch when exact post-commit files are needed. Use the returned commit as the next base for that branch.

## Branch/base semantics

### New branch

The declared `base_sha` must still equal the authoritative checkout HEAD when processed. A local human change can make a queued request stale.

### Existing safe branch

The branch tip must match the expected base unless the daemon is recovering the exact same already-committed transaction.

The bridge deliberately does not auto-rebase or auto-merge.

## Concurrency semantics

- Exactly one watcher owns mailbox transport/result publication.
- Local edit workers may overlap only across different canonical repository paths when configured with multiple workers.
- Same-repository mutations are serial.
- Materialisation is a watcher-side quiescent barrier.
- Request discovery/execution order is not FIFO.
- Use explicit result dependencies rather than ordering assumptions.

## Control requests

Doctor and diagnostics are lightweight watcher-owned operations and may be serviced while repository workers are active.

Doctor is for versions/transport/RC health/OAuth-client-presence. Diagnostics is for bounded recent sanitized metrics and scheduler counts. Neither exposes raw local paths or command output.

## Repository-index freshness

The local operator approves filesystem roots, not each repository individually. The watcher periodically discovers valid Git repositories beneath those roots and republishes `v2/meta/repos.json` when membership changes. A client should normally wait/re-read the index rather than asking for a manual per-repository registration. `scan`, `add-root`, and discovery-interval configuration are local operator controls.

An unknown-repository request does not permit the remote client to force unbounded rescanning. Discovery remains rate-limited. If a repository was replaced locally, its public ID may change so local command/push policy cannot transfer to unrelated history; re-read the index rather than assuming the old ID follows the path.
