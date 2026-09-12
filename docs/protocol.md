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

All requests are single JSON objects. The filename is `<transaction_id>.json`, and the `transaction_id` field must match it exactly.

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

## Edit transaction

Required fields:

- `protocol`: integer `2`
- `kind`: `"transaction"`
- `transaction_id`: safe filename token
- `repo`: repository ID or unambiguous repository name
- `base_sha`: full 40-character commit SHA
- `branch`: branch beginning with the locally configured safe prefix
- `patch`: unified diff text
- `run`: list of locally configured symbolic command names

Optional:

- `commit_message`
- `push`: boolean, default `false`. `true` is honoured only when push has been enabled locally for that repository.

A transaction is ready when its final JSON file is visible in `v2/transactions/`. Clients should publish the object atomically where their transport permits it. Google Drive file creation becomes visible only after upload completion, which is sufficient for the current adapter.

The daemon applies the patch with `git apply --index` in an isolated worktree. Only patch changes are staged; test-generated untracked files are never swept into the commit. Git hooks and commit signing are disabled for bridge-created commits so a remote patch cannot indirectly trigger repository hooks or block on local signing configuration.

Push is two-key: it must be enabled locally for the repository and the transaction must contain `"push": true`. The remote is fixed to `origin`, the refspec is fixed to the validated transaction branch, Git hooks are disabled, and force-push is never requested. A push failure is reported as a secondary `push.status: error` while preserving the successfully tested local commit.

## Result and idempotency

The daemon writes `v2/results/<transaction-id>.json`. A result contains `status: success` or `status: error`. Successful edit results include branch and commit information and, when publication succeeds, the remote path of the refreshed branch snapshot. Successful materialisation results include the base snapshot path.

The remote result is the durable acknowledgement. The daemon also keeps a local published-result marker. In steady state it therefore polls only the transaction directory; it consults the remote result directory only when it first encounters an unknown request. This preserves replay safety after local-state loss without paying two Drive list operations on every poll.
