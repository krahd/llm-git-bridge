# Local Executor Bridge v6

A **local-first, operator-controlled bridge** for letting an LLM or agent perform bounded shell and Git work on your Mac. Commands travel through a Google Drive mailbox; one macOS daemon validates requests, applies local permission policy, executes them, and publishes durable results. The agent does not need direct SSH access to the Mac.

**Status:** Local Executor Bridge v6 is the supported production architecture in this repository. The older protocol-v2 Git bridge remains as compatibility and historical code, **not a second service to run**. The code is available for independent installations, but this repository does not claim a universally qualified, one-click release or completed third-party macOS acceptance testing.

**Licensing:** This repository is publicly readable and cloneable, but no licence has yet been selected. Do not assume rights to redistribute or incorporate the code in another product. See [licence decision issue #82](https://github.com/krahd/llm-git-bridge/issues/82).

## What it does

- Runs read-only shell queries with filesystem and network restrictions.
- Allows scoped writes within an existing Git repository/worktree while keeping raw shell network disabled.
- Uses a trusted, bundled Git workspace coordinator for isolated branches/worktrees, durable checkpoints, controlled fetch/push/integration and remote verification.
- Requires explicit local, click-only approval for system authority and high-impact effects.
- Records STARTED/FINISHED states to prevent accidental replay after crashes or transport timeouts.

The bridge is **not** a VM, a general secure multi-tenant shell service, or a GitHub replacement. An approved system-scoped action has the logged-in Mac user's authority. Mailbox access must be treated as privileged.

## Install on your own Mac

Requirements: macOS, Python 3, Git, `rclone`, a Google Drive account, and a local folder of repositories you choose to expose. Configure a Google Drive remote with `rclone config` first; prefer your own OAuth Desktop client for sustained use.

```bash
git clone https://github.com/krahd/llm-git-bridge.git
cd llm-git-bridge
bash shell_bridge/install.sh
```

The installer asks for an existing allowed local root (for example `$HOME/repos`), chooses a configured Drive remote, creates and pins one mailbox, installs the daemon and macOS approval helper, and performs its built-in doctor/smoke procedure. Choose the narrowest practical allowed root.

**Important:** A fresh install is **not** an upgrade. If any bridge instance or durable journal already exists, do not rerun the installer against the live installation and do not start a second daemon. Follow [production operations](shell_bridge/V6_PRODUCTION_OPERATIONS.md) from an interactive Terminal, including inert staging, owner census and guarded migration. Never manage the live daemon through its own request mailbox.

The top-level `sh install.sh` is a compatibility wrapper for the same v6 installer when run from a full checkout. Old tutorials recommending the top-level bootstrap script to install protocol-v2 are obsolete.

For a complete first-install walkthrough, see [installation](docs/installation.md). For submitting and verifying requests, see the [v6 client guide](docs/v6-client-guide.md) and [offline client validator](skills/mac-git-bridge/scripts/v6_client_check.py). For architecture and security detail, see [v6 design](docs/local-executor-bridge-v6.md) and [shell bridge reference](shell_bridge/README.md).

## Connect an agent

Your agent needs authenticated permission to **upload raw JSON files** to the chosen Drive `requests/` folder and read their matching results from `results/`. Google Docs are **not** a valid request format. Some connectors support reading Drive but cannot write raw files; a file-upload tool and its file-reference handoff must be verified before relying on the bridge. A generated local file is not evidence that Drive received it.

Each request needs a unique ID and a truthful plain-language `explanation`:

```json
{
  "protocol": 1,
  "id": "example-read-2026-001",
  "cwd": "/Users/you/repos/example",
  "command": "git status --short --branch",
  "explanation": "Inspect the repository working-tree state without changing it.",
  "timeout_seconds": 30,
  "write_scope": "read_only"
}
```

Upload as `requests/example-read-2026-001.json`. Read the **exact** `results/example-read-2026-001.json` and check terminal status and `exit_code`. If the result is not yet available, inspect `health.json` once, not a tight polling loop. After ambiguous delivery, reconcile the exact request before resubmitting any mutation. Reusing an ID is never a valid retry.

The supplied [agent skill](skills/mac-git-bridge/SKILL.md) includes a portable offline request/result validator. It describes the transport, permission model, workspace coordinator, and safe recovery workflow. Each operator must configure their own allowed root and mailbox IDs; example paths/IDs are not credentials or production defaults.

## Security model

| Mode | Local authority |
| --- | --- |
| `read_only` | Filesystem reads within local policy; no network and no persistent writes |
| `repository` | Writes to one existing repository/worktree and its Git metadata; no raw shell network |
| trusted `workspace.py` | Governed Git lifecycle with network for coordinator operations only; user-supplied commands remain sandboxed |
| `system` | Full logged-in-user authority only after local approval |

`auto` resolves the minimum permitted authority locally, regardless of what the remote client requests. High-impact commands have an additional approval gate. Agents may create GitHub repositories only explicitly **private**; requests to create a public repository or change visibility to public are non-waivable rejections.

Never expose secrets in requests, disable safety checks to work around a failed command, share a mailbox with untrusted actors, or run two daemons against one mailbox. See [v6 security architecture](docs/local-executor-bridge-v6.md), [client safety and failure guidance](docs/v6-client-guide.md), and [operations](shell_bridge/V6_PRODUCTION_OPERATIONS.md).

## Develop and verify

Run the available test suites from the repository checkout:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
python3 -m unittest discover -s shell_bridge/tests
```

These tests are necessary but do **not** replace first-install and approval acceptance on a real macOS host. The installed daemon can lag GitHub source; check `health.json` for `bridge_version`, `build_source_commit` and `build_code_integrity` rather than assuming deployment.

Measured [read-only v6 client smoke evidence](docs/v6-client-smoke-2026-10-30.md) is available; it is not a substitute for full release qualification.

Contributions: [CONTRIBUTING.md](CONTRIBUTING.md). Legacy protocol-v2 installation (historical only): [archive](docs/legacy-protocol-v2-installation.md).
