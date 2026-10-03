# Local Executor Bridge v6

`llm-git-bridge` now has **one production bridge architecture**: Local Executor Bridge v6. One daemon owns the mailbox, shell execution, durable request/replay state, operator approvals, and the trusted Git workspace coordinator. Git is not a second remote bridge. Substantial repository work is a first-class capability of the same local executor.

The repository still retains the older protocol-v2 Git implementation for compatibility, migration, and historical tests, but it is **not** the recommended production architecture and should not run as a second daemon beside v6.

## Architecture

```text
LLM / agent
    |
    | request JSON
    v
transport adapter (Google Drive/rclone today)
    |
    v
Local Executor Bridge v6 — one daemon / one mailbox / one state tree
    |
    +-- sandboxed shell execution
    |      +-- read-only outside repositories
    |      +-- repository-scoped writes, offline by default
    |      +-- explicit approved system elevation
    |
    +-- trusted Git workspace coordinator
           +-- isolated branch + worktree
           +-- bounded validation
           +-- checkpoint + push
           +-- stale/conflict reconciliation
           +-- serialized integration + remote verification
```

GitHub is the canonical shared repository state. The Mac checkout/worktrees are authoritative for current local execution state. The mailbox is transport only.

## Permission and approval model

Every request carries a required plain-language `explanation`. The bridge resolves authority locally; the remote caller cannot grant itself permissions.

The execution scopes are:

- `read_only`: no filesystem writes and no network;
- `repository`: writes only to the selected Git repository/worktree and its Git metadata; raw repository shell network is denied in v6;
- trusted workspace coordinator: privileged only for bridge-owned Git/worktree/checkpoint/push lifecycle; remotely supplied `workspace exec` and integration validation payloads are sandboxed again inside the worktree and run without network or ambient credentials;
- `system`: normal logged-in-user authority, always requiring local operator approval.

`auto` resolves to the least authority that fits the request. High-impact operations are an additional approval gate, not the primary sandbox. Approval is **effect-before-command**: the dialog explains the intended action and required authority first, then exposes request ID, working directory, and exact command as inspectable details. Elevated approval is click-only, with no Return/Space shortcut selecting an action. Missing UI support fails closed.

Repository visibility is stricter than the normal approval model: agent requests that create a public repository or make a repository public are rejected outright and cannot be approved through the operator dialogue. Agent-created repositories must be explicitly private. Existing public repositories remain operable under normal policy.

Recognised high-impact operations include GitHub control-plane mutation, mutating HTTP calls, remote shell/copy, destructive Git pushes/local resets, recursive forced deletion, and non-repository system/package mutation. Ordinary substantial Git work should use the workspace coordinator instead of asking for broad shell elevation.

## Durable Git work

The same bridge ships `workspace.py`. For substantial or concurrent repository changes, use it instead of editing canonical `main` directly:

```bash
python3 ~/.local/share/local-executor-bridge/workspace.py create   --repo /path/to/repo --resource docs/example --job-id example-001 --push-initial
python3 ~/.local/share/local-executor-bridge/workspace.py exec   --job example-001 --command '...' --checkpoint-message 'Checkpoint'
python3 ~/.local/share/local-executor-bridge/workspace.py ready --job example-001
python3 ~/.local/share/local-executor-bridge/workspace.py integrate   --job example-001 --validate 'python3 -m unittest discover -s tests' --timeout 300
```

Workspace checkpoints are committed and pushed. Integration re-fetches the canonical target, checks stale/overlap/conflict conditions, validates in an isolated integration worktree, pushes the target, and independently verifies the remote ref. A conversation timeout does not imply bridge failure and never authorises replay of an ambiguous mutation.

## Request example

```json
{
  "protocol": 1,
  "id": "repo-status-001",
  "cwd": "/Users/me/repos/example",
  "command": "git status --short --branch",
  "explanation": "Check repository state before making changes.",
  "timeout_seconds": 30,
  "write_scope": "auto"
}
```

Request IDs are unique and never reused. Terminal results distinguish `completed`, `rejected`, `indeterminate`, timeout, and output-limit outcomes. STARTED without a provable FINISHED state is never silently replayed.

## Installation

```bash
git clone https://github.com/krahd/llm-git-bridge.git
cd llm-git-bridge
bash shell_bridge/install.sh
```

The source directory remains `shell_bridge/` for compatibility with existing installations and client instructions; the product/runtime identity is Local Executor Bridge v6. The installer creates/adopts one verified mailbox, pins its Drive IDs, installs one LaunchAgent, runs `doctor`, and performs an end-to-end smoke test.

The v6 installer supports staged migration. Existing bridge services are not retired until the v6 smoke test succeeds. Final cutover retires the obsolete `com.tom.chatgpt-shell-bridge` and `io.llm-git-bridge.daemon` LaunchAgents only after successful v6 acceptance.

## One bridge, not two

The historical protocol-v2 implementation remains in this repository because it contains compatibility machinery, migration evidence, and tests. Do not treat it as a second production service. New client/skill documentation should target Local Executor Bridge v6 and its workspace capability.

The separate Conversation Harness/Buork work-continuity system owns WorkThreads, provider/session continuity, browser handoff, and higher-level work state. It consumes executor/workspace capabilities; those concerns do not belong in the bridge.

## Security boundary

The bridge is least-privilege infrastructure, not a VM. Sandboxed shell/workspace payloads deny unrelated home reads, ambient credentials, network where not required, and writes outside declared roots. Explicitly approved `system` execution intentionally restores the logged-in user's authority. The mailbox is privileged infrastructure; never place secrets directly in command bytes because request/result/journal data may persist.

See [`docs/local-executor-bridge-v6.md`](docs/local-executor-bridge-v6.md), [`docs/shell-bridge-v5-architecture.md`](docs/shell-bridge-v5-architecture.md), and [`shell_bridge/README.md`](shell_bridge/README.md) for implementation and migration detail.
