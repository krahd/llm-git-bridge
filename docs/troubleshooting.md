# Troubleshooting

## `another bridge watcher is already running`

This usually means the background daemon is already doing its job.

The watcher uses an advisory `flock`; a dead process cannot keep the lock. Do **not** delete `watch.lock` to bypass it, because that can create two consumers if another process still owns the file descriptor.

On macOS, inspect the daemon instead:

```bash
launchctl print gui/$(id -u)/io.llm-git-bridge.daemon \
  | grep -E 'state =|pid =|last exit code'
```

## A request is still visible in `transactions/`

Inbox presence does not mean the watcher ignored the request. A request remains in the transaction directory while it is being processed and is removed only after a durable signed result has been published and cleanup succeeds.

For a long validation command, the same request can remain visible for minutes.

## A transaction is slow

Separate transport time from configured-command time.

Remote results include:

```json
"transport_timings": {
  "transaction_list_s": 0.2,
  "transaction_download_s": 0.5,
  "transaction_list_transport": "rcd",
  "transaction_download_transport": "rcd"
}
```

Edit results also include local `timings`, including configured command duration.

Configured command logs remain local under:

```text
~/.local/state/llm-git-bridge/command-logs/<transaction-id>/
```

The daemon logs are:

```text
~/.local/state/llm-git-bridge/daemon.out.log
~/.local/state/llm-git-bridge/daemon.err.log
```

Do not assume a slow edit means Drive is slow: after the September 2026 optimisation, the bridge repository's own full test command still accounts for most of a serious edit transaction.

## `rclone rcd unavailable; using subprocess fallback`

The bridge can continue through bounded ordinary `rclone` subprocesses, but they are slower on the reference macOS host.

Restarting the daemon creates a fresh app-private RC process:

```bash
bin/llm-git-bridge daemon restart
```

Do not repeatedly restart a daemon that is actively processing a transaction. First inspect process/log state.

A `doctor` request reports `rc_enabled` and `rc_socket_healthy`.

## `custom_drive_client_id_configured: false`

The bridge is using a Drive configuration without its own OAuth client ID. It can work in this state, but a private Google OAuth Desktop client is recommended for sustained use.

See [google-drive-oauth.md](google-drive-oauth.md).

## `stale repository base`

For a transaction that wants to create a new branch, the supplied `base_sha` no longer matches the authoritative checkout HEAD.

Refresh/materialise the repository and regenerate the patch against the current commit. The bridge does not auto-rebase stale requests.

## `stale branch base`

The target bridge branch has already advanced beyond the base named by the request.

Read/materialise the current branch tip and prepare the next transaction against that commit. Do not reuse an old request with new content under the same transaction ID.

## Tracked local modifications block a transaction

This is intentional. The bridge refuses to apply a remote patch while the authoritative checkout has tracked local modifications, because it cannot safely claim a clean base.

Commit, stash, or otherwise resolve the local work before retrying.

## `push requested but is not enabled locally`

First inspect the configured roots and their inherited policy:

```bash
llm-git-bridge roots list
```

If every repository beneath a root should be able to push validated safe branches, set that root's policy explicitly:

```bash
llm-git-bridge roots add ~/repos --push enable
```

If only one repository should differ from its root, use a repository override instead:

```bash
llm-git-bridge configure-push my-repo enable
```

Run `llm-git-bridge configure-push my-repo inherit` later to remove the exception. The remote request must still contain `"push": true`. After a policy change, remote clients should re-read `v2/meta/repos.json` and confirm `capabilities.push`.

## A test/build command timed out

Configured commands have a bounded runtime. Inspect the local command log for the transaction, fix or split the validation workload, and submit a new transaction against the appropriate base.

## Multiple clients seem out of order

The bridge is single-consumer but not FIFO. Transport listing order can differ from upload order. Dependent transactions must wait for the earlier signed result and use its commit SHA; see [concurrency.md](concurrency.md).
