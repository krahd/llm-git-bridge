---
name: mac-git-bridge
description: Use an operator-installed Local Executor Bridge v6 to inspect, test and modify Git repositories on a Mac through its Google Drive request/result mailbox. Use for local Git work requiring local working-tree state, sandboxed command execution, or durable coordinator workspaces when direct Mac filesystem access is unavailable. Verify the installed v6 build, allowed root and mailbox before submitting requests; never replay ambiguous mutations. For substantial Git changes, checkpoint in a workspace and verify intended pushed refs against the canonical remote.
---

# Mac Git Bridge (Local Executor Bridge v6 client)

This is a **distributable client template**, not a preconfigured access grant. It requires the operator's own private deployment details: allowed repository root, pinned Drive mailbox folder IDs, and a functioning authenticated raw-file upload/download client. Do not use example paths, stale conversation memory or another person's IDs as live settings.

## Authority and operating rules

1. The GitHub remote is canonical shared repository state.
2. The Mac checkout/worktrees determine current local execution state (including uncommitted work).
3. Google Drive is request/result transport only, never repository authority.
4. One Local Executor Bridge v6 daemon must own each live mailbox. Never start a legacy protocol-v2 watcher beside v6.
5. Do not reset, clean, discard, overwrite, force-push or auto-replay ambiguous commands.
6. Repository-changing work completes only after validation, commit/checkpoint, intended push and independently verified remote ref, unless explicitly local-only.

## Identify the live deployment first

Obtain the operator's **configured mailbox** and allowed root from the actual installation, not a public sample. The mailbox has `requests/`, `results/` and root `health.json`. Resolve and retain exact Drive folder IDs rather than relying on folder-name searches. Verify these current health fields:

- `bridge_version` must be `6`;
- `build_code_integrity` must be `matches_manifest`;
- `build_source_commit` identifies the **installed** build, which may be older than the GitHub main branch;
- pinned root, requests and results folder IDs must correspond to the selected mailbox;
- `active_requests` and STARTED-without-FINISHED counts inform pending/recovery work.

A stale heartbeat is advisory, not proof of a dead daemon. Check the exact result and local process/journal state before any restart. The source of truth for installation/upgrade is [V6_PRODUCTION_OPERATIONS.md](../../shell_bridge/V6_PRODUCTION_OPERATIONS.md).

## Submit one bounded raw request

Use a unique lower-case ASCII `id` containing only letters, digits, dots, underscores or hyphens (at most 128 characters). Never reuse an ID for a new request.

Create **raw UTF-8 JSON**, named `<id>.json`, in the installed mailbox `requests/` folder. Replace the example `cwd` with an existing directory under the operator-configured allowed root.

```json
{
  "protocol": 1,
  "id": "repo-status-2026-001",
  "cwd": "/Users/you/repos/example",
  "command": "git status --short --branch",
  "explanation": "Read the current Git branch and working-tree status without changing files.",
  "timeout_seconds": 30,
  "write_scope": "read_only"
}
```

Every v6 request requires a concise, bounded, truthful `explanation` of intended effect, including read-only diagnostics. It is displayed in local approval. Optional `stdin_b64` carries exact binary stdin; do not include secrets in commands, explanations or stdin since request/journal bytes may persist.

**Client capability gate:** Verify that an available tool can actually upload a raw file into the pinned Drive folder and read the matching raw result. Google Docs creation, a local temporary file, a search result, or connector metadata do not constitute a raw upload. Some Drive plugins require a tool-generated `file_uri` and do **not** accept local container paths. In the tested Google Drive connector, an exported/created file reference's `file_uri.file_id` string was accepted by `upload_file`, while passing the whole object was rejected by schema validation; inspect the actual tool schema before applying this adapter-specific rule. If the file handoff/upload fails, classify that as a client/transport blocker; **do not claim the command ran and do not fabricate a result**. A failed upload acknowledged as such may be followed by an alternative verified upload mechanism; do not blindly resubmit if delivery status was ambiguous.

Once submitted, retain the exact ID. Read the matching `results/<id>.json` rather than scanning/polling the entire folder. Check terminal `status`, `exit_code`, output and any timeout/output-limit flags. `completed` with nonzero `exit_code` is not success. If no exact result appears, read `health.json` once; pending is not failure.

## v6 authority and confirmation

- `auto` (default): local daemon resolves least appropriate authority.
- `read_only`: filesystem reads (subject to local policy), no persistent writes or network.
- `repository`: writes constrained to one existing Git repository/worktree and its metadata; **raw shell has no network**. Do not use raw repository-scope shell to fetch/push, invoke `gh`, or make HTTP calls.
- Installed trusted `workspace.py`: known coordinator subcommands are a separate capability with governed Git fetch/checkpoint/push/integration. Remote-provided workspace `exec` and integration validation are re-sandboxed without ambient credentials or network.
- `system`: user-level authority after explicit, local, click-only approval; do not request it just to bypass restrictions.

High-impact effects can require additional approval. The Mac menu-bar approval queue shows intended effects first and allows inspection of exact command details. Rejection or expiry is terminal before STARTED. Never disguise a mutation to avoid the gate. Agents may create repositories only explicitly private; publication/visibility change to public is not an approvable automated action.

## Crash and delivery recovery

- `completed`: terminal result; still check exit code and intended effects.
- `rejected`: not executed; inspect why before making a new request.
- `indeterminate`: STARTED persisted, FINISHED unproven. **Never replay automatically**; inspect local Git, filesystem and remote effects first.
- `timed_out` or `output_limited`: narrow the operation and reconcile effects before retrying.

A ChatGPT delivery timeout differs from a shell command timeout. After ambiguous tool delivery, inspect the **exact request ID** and actual Git/remote state once before any new mutation. Do not busy-poll, create competing daemons or clear lock/journal files.

## Durable repository work

For substantial/concurrent changes, use the **installed** coordinator only:

```bash
python3 ~/.local/share/local-executor-bridge/workspace.py create \
  --repo /path/to/repo --resource docs/example --job-id unique-job-id --push-initial
python3 ~/.local/share/local-executor-bridge/workspace.py exec \
  --job unique-job-id --command 'bounded validation or edit' --checkpoint-message 'Checkpoint'
python3 ~/.local/share/local-executor-bridge/workspace.py ready --job unique-job-id
python3 ~/.local/share/local-executor-bridge/workspace.py integrate \
  --job unique-job-id --validate 'repo-root-relative test command' --timeout 300
```

The coordinator creates isolated branches/worktrees and pushed checkpoints. Integration checks latest target, ancestry, changed-path/resource overlap, validation, canonical push and independent remote ref. Inspect `workspace.py list/show` on resumption to avoid duplicating jobs. Do not edit canonical `main` directly for substantive work.

## Production and safety boundary

Never run daemon installation, upgrade, cutover, consolidation, launchd maintenance, or migration **through its own mailbox**. Those operations must follow [canonical production operations](../../shell_bridge/V6_PRODUCTION_OPERATIONS.md) in an interactive operator Terminal. Preserve journals, pending approvals and old worktrees. Do not replace v6 with protocol-v2.

The bridge's sandbox is not a VM. Operator-approved `system` execution has the logged-in user's authority. Keep mailbox ACLs private and minimise permitted repository roots. Public source documentation is not proof that a particular daemon or client upload path has passed end-to-end acceptance.

Project documentation: [README](../../README.md), [installation](../../docs/installation.md), and [v6 architecture](../../docs/local-executor-bridge-v6.md).
