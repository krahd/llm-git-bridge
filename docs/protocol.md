# Protocol v2

The protocol is provider-neutral. A client needs read/write access to the selected mailbox transport.

## Layout

```text
v2/meta/repos.json
v2/repos/<repo-id>/snapshot.json
v2/repos/<repo-id>/branches/<branch-token>/snapshot.json
v2/transactions/<request-id>.json
v2/results/<request-id>.json
```

`v2/meta/repos.json` intentionally omits local filesystem paths.

All requests are single JSON objects. The filename is `<transaction_id>.json`, and the `transaction_id` field must match it exactly. Transaction IDs are 1–120 ASCII letters/digits/underscore/hyphen, starting with an alphanumeric character; dots and path components are not accepted. Duplicate JSON keys and oversized request objects are rejected.

## Materialise request

A client can activate or refresh any repository visible in the repository index without user intervention:

```json
{
  "protocol": 2,
  "kind": "materialize",
  "transaction_id": "tx-example-materialize",
  "repo": "llm-git-bridge"
}
```

The daemon refreshes that repository's local state, republishes the path-free repository index, uploads `v2/repos/<repo-id>/snapshot.json`, and writes a result containing the snapshot path and current HEAD.

A client can also materialise an already-created safe-prefixed branch without changing the user's checked-out working tree:

```json
{
  "protocol": 2,
  "kind": "materialize",
  "transaction_id": "tx-example-branch-materialize",
  "repo": "llm-git-bridge",
  "branch": "ai/example"
}
```

Branch materialisation uses an isolated detached worktree and writes `v2/repos/<repo-id>/branches/<branch-token>/snapshot.json`.

## Diagnostics request

A client can request recent sanitized timing metrics without reading daemon logs or local paths:

```json
{
  "protocol": 2,
  "kind": "diagnostics",
  "transaction_id": "tx-example-diagnostics",
  "limit": 10
}
```

`limit` defaults to 10 and is bounded to 1–50. Diagnostics expose only transaction IDs, timing/count fields, event names, and timestamps; repository paths, command output, credentials, and arbitrary local metric fields are not returned.

## Doctor request

A client can request a deliberately small, sanitized environment report:

```json
{
  "protocol": 2,
  "kind": "doctor",
  "transaction_id": "tx-example-doctor"
}
```

The report exposes only bridge/Python/Git/rclone version strings, transport type, persistent-RC health, the rclone backend type, and whether a Google Drive remote has an explicit custom OAuth client ID. It never returns the rclone configuration path, OAuth client ID value, client secret, refresh token, credentials, repository paths, or command output.

## Edit transaction

Required fields:

- `protocol`: integer `2`
- `kind`: `"transaction"`
- `transaction_id`: safe filename token
- `repo`: repository ID or unambiguous repository name
- `base_sha`: full 40-character commit SHA
- `branch`: branch beginning with the locally configured safe prefix
- `patch`: unified diff text
- `run`: list of locally configured symbolic command names (safe 1–64 character tokens)

Optional:

- `commit_message`
- `push`: boolean, default `false`. `true` is honoured only when push has been enabled locally for that repository.
- `publish_snapshot`: boolean, default `false`. When true, synchronously publish the post-commit branch snapshot before acknowledging the transaction.

A transaction is ready when its final JSON file is visible in `v2/transactions/`. Clients should publish the object atomically where their transport permits it. Google Drive file creation becomes visible only after upload completion, which is sufficient for the current adapter.

The daemon applies the patch with `git apply --index` in an isolated worktree. Only patch changes are staged; test-generated untracked files are never swept into the commit. Git hooks and commit signing are disabled for bridge-created commits. Symlink/submodule changes, protected secret paths, `.gitmodules`, and common CI/automation control paths are rejected before configured commands run.

Configured command argv remain entirely local. Remote results report only each symbolic command's name, return code, and duration; stdout/stderr stay in bounded user-private local logs. The bridge verifies staged/Git control state after each command. This does not sandbox the patched program from the user's filesystem/network; see the explicit trust boundary in `docs/security.md`.

Push is two-key: it must be enabled locally for the repository and the transaction must contain `"push": true`. The remote is fixed to `origin`, the refspec is fixed to the validated transaction branch, Git hooks are disabled, and force-push is never requested. A push failure is reported as a secondary `push.status: error` while preserving the successfully tested local commit.

Branch snapshot construction and publication are deferred by default so neither local snapshot generation nor a slow mailbox upload delays the transaction result. The result then contains `snapshot_deferred: true`; the client can issue a branch materialisation request when it needs a fresh remote snapshot. `publish_snapshot: true` retains synchronous publication for clients that explicitly require it. A post-commit snapshot-generation or publication failure is reported as a secondary `snapshot_error` and does not erase the already-created commit.

## Result and idempotency

The daemon writes `v2/results/<transaction-id>.json`. A result contains `status: success` or `status: error`. Successful edit results include branch and commit information; by default they also report `snapshot_deferred: true`. If synchronous snapshot publication was explicitly requested and succeeds, the result contains the remote snapshot path. Successful materialisation results include the requested base or branch snapshot path.

The remote result is the durable acknowledgement. Results carry a local-key HMAC authenticator; replay recovery trusts a remote acknowledgement only when that authenticator verifies. A mailbox writer can read result tags but cannot forge a valid acknowledgement for a new result without the local key. Once that acknowledgement exists, the original transaction object is no longer durable state: the daemon best-effort deletes it from the transaction inbox and also reaps previously acknowledged requests during idle polls. This keeps the hot polling directory small without weakening replay protection; local request/result copies and the remote result remain available for diagnosis. The daemon also keeps a local published-result marker. At watcher startup it reconciles the remote result directory once and rebuilds any missing local markers before processing transactions. Normal polling then lists only the transaction directory, preserving replay safety across restarts without adding a result-directory listing to every first-seen transaction. Results include transport timings known before acknowledgement and identify whether transaction listing/download used persistent `rclone rcd` or the subprocess fallback. Result-upload duration and its transport mode are recorded in the daemon's local JSONL metrics.
