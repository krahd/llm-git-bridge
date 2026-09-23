# Shell Bridge v5 client guide

This is a compact instruction template for an LLM or agent that has access to the Google Drive mailbox created by `shell_bridge/install.sh`.

## Authority hierarchy

1. GitHub remote is canonical shared repository state.
2. The host checkout is authoritative for current local execution state, including its working tree, index, local branches, worktrees, and command results.
3. Google Drive is transport only.

Never treat conversation memory, mailbox files, or a local `HEAD` alone as canonical repository state. Preserve unpublished local work; do not reset, clean, overwrite, or force-update merely to match the remote.

## Request discipline

- Use a new globally unique request ID for every request.
- Keep commands non-interactive, bounded, and comfortably below the configured timeout/output limits.
- Submit one potentially slow or mutating request at a time.
- After submission, inspect the matching result by exact ID; do not repeatedly list or poll the whole mailbox.
- If the result is not visible, read `health.json` once. If the request is active/pending, do not resubmit it.
- A conversation/tool delivery timeout is not evidence that the host command failed.
- After any ambiguous mutation, inspect authoritative filesystem/Git/remote state before retrying.

## Git mutation discipline

Before any repository mutation establish the current branch, worktree state, remotes/upstream, and expected base. Fetch the intended GitHub remote before new edits when possible. Never discard unrelated pre-existing work.

Use the installed workspace coordinator for substantial, long-running, or concurrent work:

```text
~/.local/share/chatgpt-shell-bridge/workspace.py
```

A repository-changing task is complete only when the intended change is committed, pushed to the intended remote/branch, and the remote ref is independently verified, unless the user explicitly requested local-only work.

## Result semantics

- `completed`: inspect `exit_code` and output.
- `rejected`: request never ran; correct the request and use a new ID.
- `indeterminate`: STARTED is durable but FINISHED cannot be proved; do not automatically replay a mutation.
- timed-out/output-limited: reduce scope before retrying.

## Trust boundary

Shell Bridge is a privileged local-user tunnel, not a sandbox. Never place secrets directly in request command text. Respect the operator's configured root and use the narrowest command required for the task.
