# Using Local Executor Bridge v6 from an agent

This is the supported **v6** client workflow. The old protocol-v2 transaction mailbox, repository index and `llm-git-bridge` CLI are legacy features; see [legacy usage](usage.md) and [legacy protocol](protocol.md) only when intentionally testing that implementation.

## Boundaries and prerequisites

Local Executor Bridge executes commands as the logged-in Mac user, subject to its sandbox and local approval gate. The mailbox is privileged infrastructure: restrict its Google Drive permissions. Do not publish real mailbox IDs, tokens or private repository paths in public skill bundles.

Before submitting commands, verify the actual Mac installation rather than reusing sample identifiers. A client requires:

1. A Mac with exactly one installed, running v6 daemon and a known allowed repository root.
2. A Drive client able to **upload raw bytes**, not merely create Google Docs, and fetch raw JSON results.
3. The current root, requests and results Drive IDs, verified against the daemon's `health.json`.
4. An existing `cwd` beneath the allowed root and a new, never-before-used request ID.

Read [installation](installation.md) for first-install instructions and [production operations](../shell_bridge/V6_PRODUCTION_OPERATIONS.md) for upgrades. **Never upgrade or restart the daemon through its own mailbox.**

## Prepare and verify a request

A read-only example (replace every example value):

```json
{
  "protocol": 1,
  "id": "example-status-001",
  "cwd": "/Users/you/repos/example",
  "command": "git status --short --branch",
  "explanation": "Inspect the current Git branch and working tree without changing files.",
  "timeout_seconds": 30,
  "write_scope": "read_only"
}
```

Save the exact UTF-8 bytes as `example-status-001.json`. Check the request **before** upload:

```bash
python3 skills/mac-git-bridge/scripts/v6_client_check.py request example-status-001.json
```

The validator prints a SHA-256 of those bytes. Keep the exact original file unchanged: this digest is required when verifying the result. Do not place secrets in requests or journalled explanations.

The validator is a **conservative offline client sanity check**. It cannot prove that a remote `cwd` exists, that an explanation is truthful, that a command is safe, that a connector has write access, or that the Mac daemon will grant permission. Its successful result is **not authorisation**.

## Transport: submit once, read exactly once

Upload the verified raw JSON file to the pinned `requests/` folder. Save the returned Drive file ID. An uploaded Google Doc, local file or text search hit is not an accepted request.

The client upload implementation is provider-specific. Some connectors require a generated file reference rather than a path or arbitrary text; confirm the actual tool schema and the file's destination MIME type. Do not assume the example Google Drive adapter from the [measured smoke](v6-client-smoke-2026-10-30.md) works for every client.

Retrieve **only** the matching `results/example-status-001.json`. Download raw result bytes and verify them locally:

```bash
python3 skills/mac-git-bridge/scripts/v6_client_check.py result example-status-001.json --request /path/to/original/example-status-001.json
```

Use distinct local paths when the request and result have the same basename. The validator checks `protocol`, `kind`, ID and filename, **exact uploaded request hash**, completion status, exit code and timeout/output completeness. It returns a nonzero process status on failure. A successful command is not proof of a particular Git mutation; inspect the actual repository and canonical remote separately.

If the result is missing, inspect `health.json` once and determine whether the request is pending. Do not busy-poll a whole folder or retry an ambiguous mutation. A request with STARTED but no provable FINISHED state is `indeterminate`: reconcile its effects before submitting any new operation.

## Health and deployment identity

Obtain the latest `health.json` from the installed mailbox root, then run:

```bash
python3 skills/mac-git-bridge/scripts/v6_client_check.py health health.json
```

This checks v6 identity, build manifest integrity and presence of pinned IDs; **it does not verify those IDs against your private deployment configuration**. Compare IDs explicitly and inspect the installed `build_source_commit`. Published GitHub `main` may be newer. A stale heartbeat alone does not prove daemon failure.

## Modes and durable Git work

- `read_only`: bounded filesystem reads, no persistent write or network.
- `repository`: scoped repository/worktree writes, **no raw-shell network**.
- `auto`: authority selected locally; never an escalation guarantee.
- Trusted installed `workspace.py`: isolated job branches/worktrees, checkpoint, fetch/push/integration and verification under its own policy. Remote-supplied job commands and validations remain sandboxed.
- `system`: operator-approved ordinary user authority where the local policy determines it is genuinely required; never use it to bypass repository safeguards.

For substantive work, use the [Mac Git Bridge skill](../skills/mac-git-bridge/SKILL.md) and its trusted coordinator. After edits, run tests, checkpoint/commit, integrate, push to the intended remote and independently verify the target ref. Never force-push or discard uncommitted work to manufacture a clean result.

## Failures and triage

| Symptom | Correct interpretation and action |
| --- | --- |
| Cannot create a raw JSON file in Drive | Client/connector transport blocker; no Mac execution is established. |
| `rejected` | Terminal rejection before shell execution; inspect the reason. |
| `completed` with nonzero exit | Shell command failed; inspect bounded output and relevant state. |
| `timed_out`, `output_limited`, `streams_incomplete` | Result is not complete success; reconcile side effects before reworking scope. |
| `indeterminate` | STARTED persisted, FINISHED unproven; **never replay automatically**. |
| `sandbox-exec` denial | Scope or path conflict; do not work around policy by relabelling or hiding effects. |
| Approval pending | Local operator must explicitly inspect and click; Review later is not approval. |
| Stale `health.json` | Advisory; check exact result and process/journal state before interpreting failure. |
| Failed or ambiguous Git push | Inspect the canonical GitHub remote ref before another push. |

## Acceptance boundary

A read-only mailbox smoke verifies only that route. Public release confidence additionally requires an independently executed fresh installation on a supported Mac, native Allow/Reject/expiry handling, sandbox-denial tests, timeout containment, workspace lifecycle and recovery tests. CI is necessary but cannot substitute for these operator-local gates. See [measured smoke evidence](v6-client-smoke-2026-10-30.md).
