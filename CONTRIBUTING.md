# Contributing to Local Executor Bridge v6

**Supported production architecture:** one Local Executor Bridge v6 daemon and one mailbox. The historical protocol-v2 repository Git API remains in this tree for compatibility and regression tests; it must not be installed as a second daemon.

This repository is publicly readable, but has **no explicit licence yet** ([issue #82](https://github.com/krahd/llm-git-bridge/issues/82)). Do not assume that public source visibility grants redistribution or sublicensing rights.

## Changes and trust boundaries

Read [AGENTS.md](AGENTS.md), [v6 architecture](docs/local-executor-bridge-v6.md), [security](docs/security.md), and the [canonical production operations](shell_bridge/V6_PRODUCTION_OPERATIONS.md) before making bridge changes.

Keep these invariants:

- Exactly one LaunchAgent named `net.laurenzo.local-executor-bridge` owns a live mailbox. Never start v5/protocol-v2 or a staging daemon alongside it.
- Never install, upgrade, consolidate, migrate, restart or reconfigure a live daemon **from a request submitted through that daemon**. Use the operator's interactive Mac Terminal and the guarded production procedure.
- Remote requests cannot grant themselves authority. Respect `read_only`, `repository` (raw network denied), the exact installed trusted coordinator and the explicit local approval gate for genuinely elevated effects.
- An agent may create only explicitly private repositories, and must never make one public; this boundary cannot be overridden by operator approval.
- Every request needs a truthful `explanation`. Retain unique IDs, STARTED/FINISHED durability, per-request claims, mailbox locks and non-replay of `indeterminate` mutations.
- Preserve unrelated dirty working trees and avoid force-push, reset, clean, destructive paths and hidden credential inheritance.
- The installed daemon may lag `main`. Do not report a GitHub source fix as deployed or claim end-to-end approval acceptance from CI alone.
- Keep the runtime standard-library-only unless a dependency materially improves the design and is justified.

The older [protocol-v2 documentation](docs/protocol.md) applies **only** when intentionally testing its compatibility implementation. Its transaction restrictions and symbolic command model are not descriptions of v6 shell requests.

## Development and tests

Use a clean feature branch or isolated worktree, not a production checkout's dirty `main`. Do not carry out deployment changes through an agent mailbox.

From a full checkout:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s shell_bridge/tests -v
python3 -m compileall -q src tests shell_bridge
sh -n install.sh
bash -n shell_bridge/install.sh
git diff --check
```

On macOS, run the bridge's unsandboxed/bounded regression suite as prescribed in [CI](.github/workflows/bridge-v6-qualification.yml). CI also typechecks the native approval app. Neither macOS CI nor a local Python unit test establishes ownership of a production mailbox or a completed fresh-install acceptance.

Portable agent/client tests:

```bash
python3 -m unittest discover -s shell_bridge/tests -p test_v6_client_check.py -v
python3 skills/mac-git-bridge/scripts/v6_client_check.py --help
```

The client validator is **offline and non-submitting**. See the [v6 client guide](docs/v6-client-guide.md), and add regression cases for malformed requests, mismatched result identities or digests, indeterminate results and failed commands.

## Submitting a change

1. Capture the current base ref and inspect relevant existing tests and docs.
2. Make the smallest coherent change; include tests for behaviour and failure cases.
3. Run affected tests locally, then the required suites above where available.
4. Update the user-facing README, [installation](docs/installation.md), [v6 client guide](docs/v6-client-guide.md), [skill](skills/mac-git-bridge/SKILL.md), and security/operations docs **only where the changed behaviour requires it**.
5. Submit a PR. Confirm the exact head SHA and completed CI before merging.
6. Independently verify `main` after merge. Do not silently upgrade any user's daemon.

## Historical protocol-v2 maintenance

The `src/`, `tests/`, `bin/`, legacy `docs/usage.md` and `docs/protocol.md` primarily describe protocol-v2. Preserve their security/regression invariants when editing historical code, but do not use them as the v6 installation or client instructions.
