# ChatGPT Shell Bridge v5

ChatGPT Shell Bridge v5 is the **transparent local-shell option** in this repository. It carries small JSON shell requests through a private Google Drive mailbox, executes them as the logged-in macOS user, and returns bounded JSON results. It is the component to use when an ordinary ChatGPT conversation needs to inspect or operate on repositories that stay on your Mac and no direct GitHub/local-filesystem integration is available.

This is intentionally different from the repository's protocol-v2 `llm-git-bridge`: the protocol-v2 bridge exposes a narrow, Git-specific transaction API; Shell Bridge exposes a raw local shell and therefore has a much larger trust boundary.

## What it provides

- `bridge.py`: concurrent, supervised request execution with durable STARTED/FINISHED state.
- `workspace.py`: durable Git workspaces for substantial or concurrent repository changes.
- `install.sh`: macOS installer/updater that creates or adopts one verified Drive mailbox, pins its folder IDs, installs a LaunchAgent, runs `doctor`, and performs an end-to-end smoke test.
- `health.json`: a low-cost operational surface for diagnosing the bridge without submitting another shell request.

## Security boundary

**Shell Bridge is not a sandbox.** Accepted commands run as the logged-in macOS user. The configured `ALLOWED_ROOT` limits the request's initial working directory, but the shell process itself has the permissions of that user. Treat the Drive mailbox as privileged infrastructure and do not put passwords, tokens, private keys, or other secrets directly in request command text, because request/result/journal bytes may persist.

For repository changes, use `workspace.py` rather than editing canonical branches directly. GitHub remains the canonical shared repository state; the Mac checkout is authoritative for current local execution state; Drive is transport only.

## Requirements

- macOS;
- Python 3;
- Git;
- `rclone` configured with a Google Drive remote;
- a local directory containing the repositories or files you intend to expose.

For sustained use, configure your own Google OAuth Desktop client for the rclone Drive remote rather than relying on rclone's shared client identity.

## Fresh installation

Clone this repository and run the Shell Bridge installer:

```bash
git clone https://github.com/krahd/llm-git-bridge.git
cd llm-git-bridge
bash shell_bridge/install.sh
```

On a fresh host the installer will:

1. ask which local directory ChatGPT may use as the initial working-directory root;
2. choose an existing Shell Bridge mailbox when exactly one configured rclone remote already has one;
3. otherwise choose the only configured remote, or ask you to select a remote when several exist;
4. create `ChatGPT Shell Bridge/bridge-instance.json`, `requests/`, and `results/` when no mailbox exists yet;
5. resolve and pin the exact Drive folder IDs so later operation does not depend on ambiguous folder-name lookup;
6. install the bridge under `~/.local/share/chatgpt-shell-bridge` and its private config/state directories;
7. install and start the macOS LaunchAgent;
8. run `doctor` and an end-to-end smoke request.

The installer refuses to adopt an existing `ChatGPT Shell Bridge` folder that lacks a valid instance marker, and it refuses ambiguous duplicate live mailbox folders.

### Non-interactive installation

For automation, make the trust decisions explicit:

```bash
RCLONE_REMOTE=my-drive \
ALLOWED_ROOT="$HOME/repos" \
bash shell_bridge/install.sh
```

`RCLONE_REMOTE` accepts the remote name with or without the trailing `:`. `ALLOWED_ROOT` must already exist. `--stage-only` writes the installed files/config/LaunchAgent but does not start the agent or run the smoke request.

To update an existing installation, pull or otherwise update this repository and run the same installer again. Existing bridge config is retained where appropriate, while the installed bridge/workspace code and manifest are refreshed from the checked-out commit.

## Request protocol

Create a raw JSON file named `<id>.json` in the mailbox `requests/` folder:

```json
{
  "protocol": 1,
  "id": "repo-status-001",
  "cwd": "/Users/me/repos/example",
  "command": "git status --short --branch",
  "timeout_seconds": 30
}
```

IDs must be globally unique for the mailbox. The daemon publishes the terminal result under the same filename in `results/`.

Terminal statuses are:

- `completed`: the process reached a terminal state; inspect `exit_code`;
- `rejected`: the request did not run;
- `indeterminate`: STARTED was durable but FINISHED cannot be proved; inspect filesystem/Git state before retrying any mutation;
- timed-out/output-limited executions are reported explicitly and should be retried only at a smaller scope.

Do not busy-poll the mailbox. Submit once, retain the exact request ID, inspect that exact result, and use `health.json` once when the result is not yet visible.

## Repository work and recoverability

For anything substantial, create a durable workspace rather than mutating the canonical checkout from a conversational command:

```bash
python3 ~/.local/share/chatgpt-shell-bridge/workspace.py create \
  --repo /path/to/repo --resource docs/example --job-id unique-job-id --push-initial

python3 ~/.local/share/chatgpt-shell-bridge/workspace.py exec \
  --job unique-job-id --command '...' --checkpoint-message 'Describe checkpoint'

python3 ~/.local/share/chatgpt-shell-bridge/workspace.py ready --job unique-job-id
python3 ~/.local/share/chatgpt-shell-bridge/workspace.py integrate \
  --job unique-job-id --validate 'bin/test' --timeout 300
```

Each job owns a private branch/worktree and pushed checkpoints. Integration rechecks the latest target, overlap/conflict conditions, validation, push, and remote state before declaring success.

## ChatGPT-side use

The remote ChatGPT/LLM side needs permission to create raw JSON files in the chosen Drive `requests/` folder and read the matching raw JSON files from `results/`. The agent instructions should preserve these invariants:

- GitHub remote is canonical shared repository state;
- Mac checkout is authoritative local execution state;
- Drive is transport only;
- unique request IDs are never reused;
- an ambiguous delivery/tool timeout is not evidence that a Mac command failed;
- mutations are never replayed until actual Git/filesystem/remote state proves they did not complete;
- substantial Git changes use the durable workspace coordinator;
- completion requires commit, push, and independent remote verification unless the task is explicitly local-only.

A reusable agent-instruction template is provided in [`docs/shell-bridge-v5-client-guide.md`](../docs/shell-bridge-v5-client-guide.md).

## Diagnostics

The live Drive root contains `health.json`, which reports the bridge version, pinned Drive IDs, active requests, process ceiling, and STARTED-without-FINISHED state. A stale heartbeat is advisory rather than proof of daemon death: inspect the exact request result and local LaunchAgent/process state before restarting or resubmitting.

Local state lives under:

```text
~/.config/chatgpt-shell-bridge/
~/.local/state/chatgpt-shell-bridge/
~/.local/share/chatgpt-shell-bridge/
```

See [`../docs/shell-bridge-v5-architecture.md`](../docs/shell-bridge-v5-architecture.md) and [`../docs/shell-bridge-v5-adversarial-audit.md`](../docs/shell-bridge-v5-adversarial-audit.md) for the design and threat-model details.
