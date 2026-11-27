# Workspace lifecycle, storage and operator retirement

This describes Local Executor Bridge v6's durable workspaces. **GitHub remains
canonical for repository content.** Local worktrees and LFS objects are not
individually the source of truth, but uncommitted work and unpushed payloads
must never be discarded as though they were recoverable remotely.

## Lifecycle

1. `create` / `ensure` allocates an explicitly locked Git worktree and records a
   durable job identity. New worktrees **defer Git LFS smudging** by default;
   tasks that actually need hydrated media must opt into `--hydrate-lfs` at
   creation, or explicitly hydrate only the needed files separately. No
   existing worktree is silently rewritten.
2. `exec` checkpoints and pushes to the job branch. `ready` proves a remote
   checkpoint. `integrate` or `finish` verifies the pinned remote, validates
   and records an integration commit on canonical `main` and an immutable
   receipt. `interrupted` and `conflicted` workspaces are **not retired**.
3. `lifecycle --repo <checkout>` is a read-only inventory of integrated,
   unfinished, interrupted, retired, missing and orphan-registered workspaces.
   Its age-eligible count is **not** a safe-to-delete count. It does not
   recursively inspect worktree contents or contact GitHub.
4. After at least 14 days, an **operator-local, exact-job** retirement can
   inspect a fully integrated job. It does not remove job branches, remote
   branches, receipts, metadata or shared LFS objects. There is no bulk
   retirement and no remote-triggered destructive GC.
5. A durable `retirement_intent` is written **after all preservation checks but
   before unlock/remove**. On operator-side interruption, a subsequent exact
   command can re-lock and recheck an existing directory, or reconcile a
   vanished directory against the original intent and preserved Git refs.
   Missing directories without intent remain unresolved, not automatically
   successful cleanups.

## Read-only status

```bash
python3 shell_bridge/workspace.py --state-dir "$HOME/.local/state/chatgpt-shell-bridge" \
  lifecycle --repo "$HOME/tom-repos/professional-opportunities"
```

## Operator-only retirement

The active production daemon must be **stopped using the canonical production
operations procedure** before any deletion. Never stop/upgrade/restart the
service from its own mailbox, and never execute these commands remotely.
Use a real operator Terminal; the tool also verifies absence of the daemon
process and current open handles. Inspect one exact job and its evidence first.

```bash
python3 shell_bridge/workspace.py --state-dir "$HOME/.local/state/chatgpt-shell-bridge" \
  gc --repo "$HOME/tom-repos/professional-opportunities" \
  --job EXACT_JOB_ID --confirm-job EXACT_JOB_ID --enable-maintenance-gc
```

For a genuinely pre-receipt integration, `--allow-legacy` is possible only
with separate explicit consent. The integration must have matching canonical
trailers and exact remote checkpoint. A **modern** integration lacking its
receipt is never eligible through this compatibility flag.

### Preservation checks and refusals

- Exactly one job under the coordinator's expected `.worktrees/<repo-id>`.
  Original registration must be locked with a matching branch and HEAD.
- Integrated metadata, checkpoint, retention age, immutable receipt (or
  explicitly approved legacy case), commit-message identity, current main
  ancestry and current remote checkpoint are independently verified.
- Git's status, index flags, ignored files and nested filesystems are
  inspected. Unknown ignored files or unknown external filters block deletion.
- LFS tracked pointers must parse. Hydrated files must hash to their pointers,
  and **shared LFS objects must also exist and pass the same SHA-256 hash**.
  Deferred-smudge pointer-only files contain no extra payload and can be
  retired without fetching objects. This does **not** establish remote GitHub
  LFS availability; that must be assessed independently before purging any
  shared LFS store. This GC **never purges** the LFS store.
- The worktree must have no open file handles according to `lsof`, and the
  production bridge must be unloaded. Operator TTY and explicit matching job
  confirmation are mandatory. Noncooperating user processes may still race;
  there is no absolute protection against arbitrary local adversarial writes.
- No `--force`, `rm -rf`, repository-wide GC, or remote branch deletion.
  Failures raise nonzero status; partial effects leave durable evidence.

### Deferred and optional work

No recurring destructive cleanup service is installed automatically. The safe
read-only inventory can be checked periodically; operator retirement remains
an explicit local maintenance step. The 88 historical integrated jobs are **age
candidates**, not pre-authorised deletions. Legacy receipts, LFS remote
availability and the safety of each worktree require individual scrutiny.

On macOS, qualify source changes using both Python test suites, the native
macOS CI lane, and the canonical
[V6 production operations](V6_PRODUCTION_OPERATIONS.md) before upgrading the
single production LaunchAgent. This source change is not a deployed service.
