# ChatGPT Shell Bridge v5

ChatGPT Shell Bridge v5 is the **transparent local-shell option** in this repository. It carries small JSON shell requests through a private Google Drive mailbox, executes them as the logged-in macOS user, and returns bounded JSON results. It is the component to use when an ordinary ChatGPT conversation needs to inspect or operate on repositories that stay on your Mac and no direct GitHub/local-filesystem integration is available.

This is intentionally different from the repository's protocol-v2 `llm-git-bridge`: the protocol-v2 bridge exposes a narrow, Git-specific transaction API; Shell Bridge exposes a raw local shell and therefore has a much larger trust boundary.

## What it provides

- `bridge.py`: concurrent, supervised request execution with durable STARTED/FINISHED state.
- `workspace.py`: durable Git workspaces for substantial or concurrent repository changes.
- `install.sh`: macOS installer/updater that creates or adopts one verified Drive mailbox, pins its folder IDs, installs a LaunchAgent, runs `doctor`, and performs an end-to-end smoke test.
- `health.json`: a low-cost operational surface for diagnosing the bridge without submitting another shell request.

## Security boundary

**Filesystem write sandbox by default.** Shell Bridge v5 now treats raw shell execution as read-only unless the request is already inside an existing Git repository/worktree or is an invocation of the trusted workspace coordinator. On macOS, the child shell runs under `sandbox-exec`: read-only mode denies filesystem writes (apart from temporary runtime locations) **and network access**, while repository mode permits writes only to the current repository/worktree, its Git metadata, and temporary runtime locations. The workspace coordinator receives a bounded repository/state scope so it can create and operate its isolated worktrees. A request may explicitly ask for `"write_scope": "read_only"`, `"repository"`, or `"system"`; `auto` is the default. `system` write scope is never silent: it requires operator confirmation before `STARTED`.

**High-impact operator confirmation.** On macOS, Shell Bridge pauses recognised high-impact control-plane/destructive commands *before* recording `STARTED` and presents a native confirmation dialogue to the logged-in operator. The dialogue shows the request ID, risk category, working directory, and actual command; **Cancel** is the default. Declined or timed-out requests are terminally `rejected` and never reach the shell. Built-in recognition covers GitHub repository/content control-plane changes, mutating `gh api` requests, obvious mutating `curl`/`wget` forms, direct `ssh`/`scp`/`sftp`/`rsync`, force/delete pushes, `git reset --hard`, forced `git clean`, recursive forced `rm`, and common non-repository filesystem/system/package mutations. On non-macOS hosts, recognised commands are rejected by default. Set `operator_confirmation_mode` to `auto` (default), `dialog`, `reject`, or explicitly `off`; `off` weakens this safeguard. Command recognition remains defence in depth: generic read-only execution has no network access, while repository-scoped network access can still produce remote side effects and therefore retains explicit classification and operator-confirmation rules.

**The sandbox is scoped, not a VM.** Generic read-only requests deny both filesystem writes and network access. Repository scope retains network access for Git/repository work, while approved `system` requests run with the logged-in macOS user's normal authority. Treat the Drive mailbox as privileged infrastructure and do not put passwords, tokens, private keys, or other secrets directly in request command text, because request/result/journal bytes may persist.

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
  "timeout_seconds": 30,
  "write_scope": "auto"
}
```

`write_scope` is optional. `auto` means repository-scoped writes with network access when `cwd` is inside an existing Git repository/worktree, and filesystem-and-network read-only otherwise. Use `read_only` to force read-only execution, `repository` to require a Git repository/worktree scope, or `system` for an intentional write beyond repository scope; `system` triggers the native operator confirmation gate. Obvious non-repository write commands detected under `auto` are also promoted to the confirmation gate instead of being run silently.

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
