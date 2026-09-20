---
name: mac-git-bridge
description: Use Tomas Laurenzo's installed ChatGPT Shell Bridge to give ordinary ChatGPT web conversations raw shell and Git access to repositories on his Mac through Google Drive. Trigger whenever a conversation needs to inspect, edit, test, commit, branch, merge, rebase, fetch, pull, push, or otherwise operate on local repositories under /Users/tom/tom-repos or /Users/tom/tom-repos/projects, especially when Developer Mode, Work, Codex, or the GitHub connector are unavailable. Treat GitHub as the canonical repository state, the Mac checkout as authoritative for current local working state, and Drive only as transport. For substantial concurrent work, use the v5 workspace coordinator so conversation lifetime is decoupled from durable Git work. Repository-changing work is not complete until intended commits are pushed and the GitHub remote state is verified, unless the user explicitly requests local-only work.
---

# Mac Git Bridge

Use the installed Google Drive ChatGPT Shell Bridge as the normal path from a web conversation to Tomas's Mac. Do not fall back to the old semantic LLM-Git-Bridge protocol unless explicitly requested.

## Authority hierarchy

Keep these roles distinct:

1. **GitHub remote is canonical repository state.** Published branches/commits on the intended GitHub repository are the canonical shared project record.
2. **The Mac checkout is authoritative for current local execution state.** Its working tree, index, local branches, uncommitted changes, worktrees, and command results determine what can safely happen next.
3. **Google Drive is transport only.** The shell-bridge mailbox carries commands/results; it is not a repository or source of truth.

Never treat conversation memory, Drive artefacts, or a local `HEAD` alone as canonical repository state. Preserve unpublished local work even when GitHub is canonical: do not reset, clean, overwrite, or force-update merely to match the remote.

For repository-changing work the default completion gate is: intended change committed -> pushed to the intended remote/branch -> remote ref independently verified. Skip commit/push only for explicitly local-only work or read-only tasks.

## Canonical v5 transport

Live bridge identity is pinned by Drive IDs, not folder-name resolution:

- Root ID: `1PRsQHgVsgXhIYT_8akGFRGdhdlw9Lmk6`
- Requests ID: `1jvTV0g4JVypFJ5olOovIPtCRIlawl4MP`
- Results ID: `1SMdHzWEj2w2zRcOsCBc6fGT44ltco1fL`
- Human path: `/Google Drive/llm-git-bridge/ChatGPT Shell Bridge`
- Health: `health.json` at the live root.

The daemon executes accepted commands with `/bin/zsh -lc` as Tomas and returns exact shell results. This remains raw shell authority, not a Git abstraction.

`health.json` is the first diagnostic surface when the request path appears unhealthy. It reports bridge version, pinned Drive IDs, active requests, process ceiling, and STARTED-without-FINISHED state without requiring a shell request.

## Repository locations

The repository universe is rooted at `/Users/tom/tom-repos`; `/Users/tom/tom-repos/projects` is nested inside it. Repositories exist at both levels. Discover uncertain paths with bounded reads, never a recursive scan of the whole home directory.

## Request protocol

Create a raw JSON file named `<id>.json` in the requests folder. IDs must be unique lowercase ASCII, start with a letter or digit, use only letters/digits/`.`/`_`/`-`, and be at most 128 characters. Never reuse an ID.

```json
{
  "protocol": 1,
  "id": "interaction-commons-status-001",
  "cwd": "/Users/tom/tom-repos/projects/interaction-commons",
  "command": "git status --short --branch",
  "timeout_seconds": 30
}
```

Optional exact binary stdin may be supplied as `stdin_b64`. Upload requests as raw JSON files, not Google Docs, then read the matching raw JSON result.

## Result and crash semantics

Treat results as authoritative local execution evidence, not canonical repository state.

- `completed`: process reached a terminal state; still inspect `exit_code`.
- `rejected`: request never ran; fix it and use a new ID.
- `indeterminate`: STARTED was durable but FINISHED cannot be proved. Never replay a mutation automatically. Inspect actual filesystem/Git/remote state first.
- `timed_out` or `output_limited`: shrink the operation; do not repeat the same scope blindly.

v5 records active process-group identity. On timeout/restart it attempts descendant containment; a STARTED request without a proven FINISHED state is never silently re-executed.

## Concurrency and execution limits

v5 is **concurrent**, not single-worker. Each accepted request runs as its own supervised process group. The daemon admits multiple requests up to a bounded configured ceiling (`max_active_requests`; currently auto-resolved by health). Independent raw shell requests may overlap.

Do not confuse transport concurrency with safe Git concurrency. Raw shell does not infer repository semantics. For long-lived or concurrent repository mutation, use the workspace coordinator described below.

Design each request to stay comfortably within:

- maximum command timeout: 300 s;
- request: 2 MiB;
- command text: 256 KiB;
- decoded stdin: 1 MiB;
- stdout/stderr: 16 MiB each.

Use non-interactive commands and small resumable requests. The bridge is non-PTY. All rclone operations are bounded by a transport timeout.

## Conversation delivery-timeout discipline

A ChatGPT **“Message delivery timed out. Please try again.”** error is not, by itself, evidence that the Mac bridge failed. Avoid turning a UI/orchestration timeout into a bridge incident.

- Keep each orchestration step bounded. Do not keep one assistant turn alive with long chains of Drive searches, repeated folder listings, or speculative follow-up canaries.
- After uploading a request, record its exact request ID. Check the matching result directly by exact ID/name. Do not busy-poll the whole results folder in a tight loop.
- If the expected result is not yet visible, inspect `health.json` once. If health is current and the request is active/pending, treat the bridge as healthy and the request as pending; do not resubmit it.
- If a ChatGPT message-delivery timeout occurs, first inspect the authoritative result for every request already submitted in that turn. Never repeat a mutation merely because the assistant response timed out.
- Distinguish layers explicitly: `timed_out: true` in a bridge result means the **Mac command** timed out; a ChatGPT “Message delivery timed out” error means the **conversation/tool-orchestration response** failed to deliver. Recover them differently.
- Prefer one bounded request plus one verification request over multi-purpose shell chains. For long tests/builds, split work or use the durable workspace/checkpoint path rather than repeated connector polling.
- Once the predefined completion/acceptance gates pass, stop. Do not add exploratory canaries, redundant health probes, or another full test cycle unless a specific gate failed or new evidence requires it.
- After any ambiguous delivery failure, reconcile actual Git/Drive/runtime state first, persist the exact next action when needed, then execute only the missing step.

The goal is to minimise total orchestration latency and duplicate side effects: **submit once, identify exactly, inspect reality, and avoid polling loops.**

## Durable workspace coordinator

Installed coordinator: `/Users/tom/.local/share/chatgpt-shell-bridge/workspace.py`.

Use it for substantial editing, for work likely to span context windows, and whenever multiple conversations may operate on the same repository. It provides a durable job branch + Git worktree + job metadata, periodic pushed checkpoints, conflict/reconciliation detection, and a short canonical integration gate.

Core invariants:

- A conversation does **not** edit canonical `main` directly for substantial work.
- Each job gets its own branch/worktree.
- Different jobs in a large repository (for example different papers inside `research/`) may work concurrently.
- Repository identity is not treated as a lock boundary. Logical `resource` keys and changed-path overlap detect relevant collisions.
- Checkpoints are committed and pushed so work survives chat/context/worktree loss.
- Canonical-branch integration is serialized briefly per repo+target branch.
- Integration fetches the latest target, verifies ancestry/staleness, checks path/resource overlap, optionally requires explicit reconciliation, validates in a detached integration worktree, creates a canonical commit, pushes, and verifies remote state.
- A failed validation or conflict leaves the job recoverable; it must not silently partially integrate.

Useful commands:

```bash
python3 /Users/tom/.local/share/chatgpt-shell-bridge/workspace.py create \
  --repo /path/to/repo --resource papers/example --job-id <unique-id> --push-initial

python3 /Users/tom/.local/share/chatgpt-shell-bridge/workspace.py exec \
  --job <id> --command '<bounded command>' --checkpoint-message '<message>'

python3 /Users/tom/.local/share/chatgpt-shell-bridge/workspace.py ready --job <id>

python3 /Users/tom/.local/share/chatgpt-shell-bridge/workspace.py integrate \
  --job <id> --validate '<repo-root validation command>' --timeout <seconds>

python3 /Users/tom/.local/share/chatgpt-shell-bridge/workspace.py show --job <id>
python3 /Users/tom/.local/share/chatgpt-shell-bridge/workspace.py list
python3 /Users/tom/.local/share/chatgpt-shell-bridge/workspace.py recover --repo /path/to/repo
python3 /Users/tom/.local/share/chatgpt-shell-bridge/workspace.py gc --repo /path/to/repo
```

Integration validation runs from the temporary repository root. Paths in `--validate` must therefore be repository-root-relative.

### Conversation death / context exhaustion

Conversation lifetime and work lifetime are deliberately decoupled. Before a long/context-heavy step, checkpoint. If a conversation disappears, inspect `workspace.py list/show` and the job's pushed branch before creating new work. Resume the existing job when appropriate; do not assume missing chat context means missing work.

If a recorded worktree is missing but the checkpoint branch is present remotely, the pushed branch remains the durable recovery source. Do not discard or overwrite it. Reconcile/recover deliberately.

## Git operating rules

Before mutation, establish branch, worktree state, remotes/upstream and relevant expected base. Identify the intended GitHub remote rather than assuming `origin` when multiple remotes exist.

Synchronise from GitHub before new edits unless explicitly offline/local-only. Fetch first. Fast-forward a clean branch only when doing so cannot overwrite unpublished work. If there are uncommitted changes, local-only commits, detached HEAD, multiple worktrees or divergence, preserve them and reconcile deliberately.

Never discard, reset, clean, stash, overwrite, or incorporate unrelated pre-existing work just to make a task easier. Use isolated worktrees/branches when appropriate.

Before push, verify the target remote/branch. Do not force-push unless the task genuinely requires history rewriting and the target has been inspected. After push independently verify with `git ls-remote` or an equivalent fetched-ref comparison.

For ambiguous push/mutation outcomes, inspect actual remote/filesystem state before another mutation.

## Standard substantial-work workflow

1. Resolve exact repo path and inspect current state.
2. Fetch the intended GitHub remote and reconcile the safe base.
3. For substantial/concurrent work, create or resume a workspace job with a narrow logical resource key.
4. Work in bounded commands and checkpoint coherent progress; pushed checkpoints are the durability boundary.
5. Before context-heavy work, explicitly checkpoint.
6. Mark the finished checkpoint ready.
7. Integrate through the workspace gate with appropriate validation.
8. Independently verify the canonical remote ref and intended content.
9. Garbage-collect only jobs whose integration is verified and whose worktree is safe to remove.

For tiny, inherently atomic repository changes, ordinary Git on an appropriately isolated clean branch remains acceptable; raw bridge concurrency itself is never evidence that concurrent mutations are safe.

## Drive hygiene

Do not reuse request IDs. Consumed requests are normally removed; results are durable evidence and should not be deleted casually. The only folder named exactly `ChatGPT Shell Bridge` should be the live mailbox. Release packages and legacy mailbox evidence belong in clearly named archive/deprecated folders, never as competing live roots.

## Trust boundary and residual OAuth warning

The bridge runs as Tomas's macOS user. The initial `cwd` must be under `/Users/tom/tom-repos`, but this is not a sandbox. Treat the Drive mailbox as privileged. Never put passwords, tokens, private keys or other secrets directly in request commands because command bytes and journal state may persist.

The currently functioning `chatgpt-git-bridge:` rclone remote still reports rclone's 2026 retirement warning for the shared Google Drive OAuth client ID. This is an external credential/configuration risk, not a v5 concurrency defect. Surface it in diagnostics and migrate to a private OAuth client when credentials/authorisation are available; do not silently alter credentials.
