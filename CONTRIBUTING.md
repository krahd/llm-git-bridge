# Contributing

`llm-git-bridge` is intentionally conservative about remote authority. Changes should preserve that property unless a new capability is explicitly designed, documented, and tested.

## Development setup

The runtime requires Python 3.11+ and has no third-party Python dependencies.

Run directly from the repository:

```bash
bin/llm-git-bridge status
```

Run the complete test suite:

```bash
bin/test
```

On Python 3.12+, `bin/test` also reports the slowest tests using `unittest --durations`.

## Project invariants

Changes should preserve these defaults:

- core protocol and terminology remain provider-neutral;
- authoritative repositories remain local;
- local filesystem paths are not published in the remote repository index;
- remote transactions cannot supply arbitrary shell/argv;
- stale SHA checks fail closed;
- safe branch-prefix validation remains enforced;
- tracked-dirty authoritative checkouts block remote patch application;
- snapshots exclude `.git`, untracked contents, common secrets, binaries, and symlinks;
- remote patching cannot modify symlinks/submodules, `.gitmodules`, or protected CI/automation control paths;
- push remains local opt-in plus per-transaction opt-in, safe-branch-only, non-force, and `origin`-only;
- merge/default-branch mutation is outside the remote protocol;
- only one local mailbox watcher executes requests at a time;
- durable result/replay recovery remains authenticated and idempotent;
- ambiguous Drive writes are never "fixed" by blindly issuing a second mutating write;
- the runtime remains standard-library-only unless a dependency provides a compelling, documented benefit.

## Security review

Any change that increases remote authority should update [docs/security.md](docs/security.md) and receive explicit adversarial review.

Remember that configured test/build commands are an integrity boundary, not an OS sandbox. They can execute patched code with the local user's privileges.

## Performance review

On the reference macOS host, process creation is expensive enough that repeated tiny Git/Python subprocesses materially affect latency. Prefer obtaining several pieces of Git state from one documented command when that does not weaken validation.

Do not improve speed by removing stale-state, path/mode, command-invariant, or result-authentication checks. Add or retain regression tests for equivalent semantics.

See [docs/performance.md](docs/performance.md).

## Before submitting a change

At minimum:

```bash
bin/test
python3 -m compileall -q src scripts tests
git diff --check
sh -n bin/llm-git-bridge bin/test
```

Where available, additional lint/type/shell tooling is welcome, but the repository should remain runnable without adding those tools as runtime dependencies.

## Documentation

Public behaviour changes should update the relevant documentation:

- setup: `docs/installation.md`;
- user workflow: `docs/usage.md`;
- wire format: `docs/protocol.md`;
- concurrency: `docs/concurrency.md`;
- security: `docs/security.md`;
- performance: `docs/performance.md`;
- operational failures: `docs/troubleshooting.md`.
