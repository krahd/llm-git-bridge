# llm-git-bridge

`llm-git-bridge` lets an LLM or agent work safely with Git repositories that remain on your own machine.

It exposes a deliberately small mailbox protocol for repository discovery, filtered snapshots, patch transactions, local validation commands, commits, and optional safe-branch pushes. Google Drive through `rclone` is the first transport, but the core protocol is provider-neutral: the remote client does not need direct filesystem access, a GitHub token, or arbitrary shell access on the host.

> **Status:** `1.0.0rc5` protocol-v2 release candidate, built on the released `v1.0.0-rc4` concurrency baseline. RC5 adds bounded automatic repository discovery inside explicitly approved local roots and hardens post-durable publication/cleanup error handling. Local transaction execution remains bounded and may overlap across different canonical repositories; same-repository mutation remains serial. Existing and new configurations default to `max_workers=1` unless the operator explicitly enables concurrency.

## Why use it?

A native Git hosting integration is usually the simplest option when it is available and has the permissions you need. `llm-git-bridge` exists for cases where you want a different boundary:

- the authoritative checkout must remain local;
- a ChatGPT/LLM session cannot access GitHub directly, or is limited to read-only access;
- edits should be tested against the local repository before they become commits;
- remote clients should never receive arbitrary command execution;
- pushes should be opt-in, branch-restricted, and local-policy controlled;
- several LLM sessions or providers should be able to submit work to the same host through one neutral protocol.

The bridge is **not** a shell proxy, a Git hosting service, or an OS sandbox.

## How it works

```text
LLM / agent session
       |
       |  request JSON / filtered snapshots
       v
mailbox transport (Google Drive via rclone today)
       |
       v
single local watcher
       |
       +--> validate request + stale base
       +--> isolated Git worktree
       +--> apply/stage patch
       +--> run locally configured checks
       +--> commit to safe branch
       +--> optional push to origin
       |
       v
signed result JSON
```

Repositories stay local. The remote mailbox contains a path-free repository index, filtered snapshots on demand, request objects, and signed results. `.git` history and common secrets are not mirrored.

Approved roots are an ongoing trust boundary, not a one-time import list. While the watcher runs, it periodically discovers newly created or cloned Git repositories beneath those roots and republishes the path-free index only when membership changes. `scan` remains available as an explicit full refresh/debug operation; adding a completely new filesystem root is still a local operator action.

## Quick start

### Requirements

- Python 3.11+
- Git
- `rclone`
- a configured Google Drive remote (for the current transport)

Clone the repository and use the bundled wrapper; installation as a Python package is optional.

```bash
git clone https://github.com/krahd/llm-git-bridge.git
cd llm-git-bridge

bin/llm-git-bridge setup --remote YOUR_RCLONE_REMOTE
bin/llm-git-bridge add-root ~/repos
bin/llm-git-bridge status
```

Configure symbolic validation commands per repository. Remote requests may refer to these names, but may not supply shell commands or argv themselves.

```bash
bin/llm-git-bridge configure-command my-repo test \
  python3 -m unittest discover -s tests -v
```

Push is disabled by default. To permit explicit transaction requests to push their validated safe branch to `origin`:

```bash
bin/llm-git-bridge configure-push my-repo enable
```

Run the watcher in the foreground:

```bash
bin/llm-git-bridge watch
```

On macOS, install the bundled LaunchAgent and let it run continuously:

```bash
bin/llm-git-bridge daemon install \
  --command "$PWD/bin/llm-git-bridge"
```

For a fuller setup guide, including Google OAuth recommendations, see [docs/installation.md](docs/installation.md).

## Request example

A transaction is one JSON object containing an inline unified diff:

```json
{
  "protocol": 2,
  "kind": "transaction",
  "transaction_id": "tx-example-20260913-a7f2",
  "repo": "my-repo",
  "base_sha": "0123456789abcdef0123456789abcdef01234567",
  "branch": "ai/example-change",
  "patch": "diff --git ...",
  "run": ["test"],
  "commit_message": "Document the example",
  "push": true
}
```

The bridge checks the repository and base SHA, creates an isolated worktree, applies and validates the patch, runs only locally configured symbolic commands, commits, optionally pushes the safe-prefix branch, then publishes an authenticated result.

## Multiple clients and ChatGPT sessions

Multiple remote clients can submit requests to the same mailbox. One local watcher always owns mailbox transport and result publication. Setting `max_workers` above 1 permits bounded local transaction overlap only across different canonical repository paths; the default remains serial (`max_workers=1`). Use `bin/llm-git-bridge configure-concurrency --workers 2 --max-pending-jobs 8` to enable the recommended initial concurrent setting after validating it on the host.

Important consequences:

- use globally unique transaction IDs for independent requests;
- use separate branches for independent edits;
- two requests may share the same base commit if they target different new branches;
- a request against an already-advanced branch is rejected as stale rather than silently rebased or merged;
- queue order is **not guaranteed to be FIFO**;
- a long validation command blocks later requests for the **same repository**, but independent repositories can run concurrently when `max_workers > 1`;
- the watcher uses round-robin repository admission within a bounded validated backlog, so a same-repository burst does not monopolise available worker slots.

A five-request live probe on the reference host completed all five successfully and demonstrated non-FIFO ordering. See [docs/concurrency.md](docs/concurrency.md).

## Performance

The September 2026 performance programme reduced the original **373.2 s / 139-test** live validation baseline to a defensible `0.3.0` gold qualification median of **68.0 s / 196 tests** across three identical-tree production runs (**67.3–71.2 s**). The earlier A3 acceptance reached 50.4 s once, but A4 deliberately does not treat that fast tail as an SLA. The gold median is about **5.5x faster** and **81.8% less wall-clock time** than the original baseline while materially expanding correctness, replay, crash-recovery, transport, and adversarial coverage.

After the final daemon restart, lightweight control-plane requests used the persistent `rclone rcd` path with transaction-directory listing around **0.23 s** and small request download around **0.44 s** in the post-promotion doctor/materialisation checks. Real edit latency is then dominated by the repository's configured validation commands, not the mailbox itself.

These are reference-host measurements, not an SLA. See [docs/performance.md](docs/performance.md) for methodology, caveats, and comparison with native Git hosting integrations.

## Security model

The bridge intentionally exposes less remote authority than a shell or unrestricted Git credential.

By default it:

- keeps repository filesystem paths local;
- uploads only filtered tracked text content in snapshots;
- omits `.git` history, untracked contents, common credentials, private keys, service-account material, binaries, symlinks, and oversized files;
- rejects stale base SHAs and tracked-dirty authoritative checkouts;
- restricts remote-created branches to a safe prefix (`ai/` by default);
- rejects symlink/submodule changes and protected CI/automation paths;
- accepts only locally configured symbolic command names;
- disables push unless it is locally enabled for the repository and explicitly requested by the transaction;
- never implements force-push or remote-triggered merge;
- authenticates durable results and binds bridge commits to the complete request identity.

**Configured validation commands are not sandboxed.** Patched code executed by a configured test/build command runs with the local user's filesystem and network privileges. Use a VM/container/separate account if you need an OS-level trust boundary.

Read [docs/security.md](docs/security.md) before enabling push or running validation commands on untrusted patches.

## Documentation

- [Installation and setup](docs/installation.md)
- [Using the bridge](docs/usage.md)
- [LLM client skill](skills/llm-git-bridge-client/SKILL.md)
- [Concurrency and multiple clients](docs/concurrency.md)
- [Performance](docs/performance.md)
- [Architecture](docs/architecture.md)
- [Scheduler architecture](docs/scheduler-architecture.md)
- [Transaction state machine](docs/transaction-state-machine.md)
- [Protocol v2](docs/protocol.md)
- [Security model](docs/security.md)
- [Google Drive OAuth migration](docs/google-drive-oauth.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Development history / project state](docs/project-state.md)
- [Contributing](CONTRIBUTING.md)

## Development

Run the complete test suite with:

```bash
bin/test
```

The runtime deliberately uses only the Python standard library. See [CONTRIBUTING.md](CONTRIBUTING.md) for project invariants and validation expectations.
