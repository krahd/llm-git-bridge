---
name: llm-git-bridge-client
description: Use llm-git-bridge as a remote LLM/agent client to discover local Git repositories through the mailbox, materialise filtered snapshots, submit protocol-v2 patch transactions, request doctor/diagnostics, interpret results, handle stale bases and crash-safe retries, coordinate multiple clients, and respect branch/push/security boundaries. Trigger whenever an LLM needs to read, edit, validate, commit, or optionally push work through llm-git-bridge rather than direct filesystem/Git access.
---

# LLM Git Bridge Client

Use the bridge as a **mailbox protocol**, not as shell access and not as a substitute for GitHub merge/review policy.

## Start every session by grounding state

1. Identify the mailbox root and transport available to the current client.
2. Read `v2/meta/repos.json` before naming repositories from memory. Treat each entry's `capabilities.read`, `capabilities.edit`, and `capabilities.push` as the current effective local authority. Re-read the index when a repository was just created/cloned, after the operator changes local policy, or when an unknown-repository response suggests your cached index is stale; repositories beneath operator-approved roots are discovered automatically on a bounded interval.
3. If operational state matters, submit `doctor` and, when useful, `diagnostics` requests.
4. Before preparing an edit, materialise the repository or relevant safe-prefix branch and read its fresh filtered snapshot.
5. Treat the snapshot's Git identity as authoritative remote context. Never invent local filesystem paths.

Read [references/protocol-workflow.md](references/protocol-workflow.md) for exact request/result workflow and [references/examples.md](references/examples.md) for canonical JSON shapes.

## Choose the correct operation

- Need current repository contents or HEAD: submit `materialize` without `branch`.
- Need an already-created safe branch: submit `materialize` with that safe-prefix branch.
- Need environment/transport health: submit `doctor`.
- Need bounded scheduler/timing evidence: submit `diagnostics`.
- Need to modify a repository: submit `transaction` with an exact full `base_sha`, safe branch, unified diff, symbolic command names, and optional push/snapshot flags.

Do **not** put a protected/default branch such as `main` in the `branch` field merely to read it. Current-checkout materialisation omits `branch`; explicit branch materialisation is for allowed safe-prefix branches.

## Construct requests fail-closed

For every request:

- use protocol `2`;
- create a globally unique `transaction_id` for independent work;
- make the request filename exactly `<transaction_id>.json`;
- use one complete JSON object with no duplicate keys;
- publish the complete file, never a partial/streaming JSON object;
- never include local paths, credentials, shell commands, command argv, or secrets;
- never reuse a transaction ID for changed request bytes.

For edit transactions additionally:

- use the exact 40-character base commit from fresh authoritative state;
- use a branch under the configured safe prefix (commonly `ai/`);
- send a unified diff as `patch`;
- request only locally configured symbolic commands in `run`;
- treat `push` as two-key opt-in: the selected repository's published `capabilities.push` must be true **and** the request must contain `"push": true`; if the capability is false, do not probe by submitting a doomed push request—report that local root/override policy must be changed, then re-read the index;
- leave `publish_snapshot` false unless the next step truly requires synchronous snapshot publication.

The bridge never accepts arbitrary remote shell execution, force-push, remote-triggered merge, alternate remotes/refspecs, or protected-branch mutation.

Do not ask a remote client to register a newly created repository individually. If it is beneath an already approved root, allow the watcher's automatic discovery to publish it and inherit root policy. Adding/removing a filesystem root, changing root policy, changing the discovery interval, or forcing `scan` remains a local operator action. Never infer or request a local root path from the path-free public index.

## Upload and wait for the durable result

1. Upload the final request into `v2/transactions/` with the exact transaction filename.
2. Do not treat continued inbox presence as failure; requests stay visible while processing and until signed-result publication/cleanup succeeds.
3. Wait for `v2/results/<transaction_id>.json`.
4. Check at minimum:
   - matching `transaction_id`;
   - `kind: "result"` when present;
   - terminal `status`;
   - operation-specific fields such as `head`, `commit`, `branch`, command results, push status, snapshot fields, and errors.
5. For a successful edit, use the returned commit as the next dependency/base where applicable.

**Do not claim that the remote LLM independently verified the result HMAC merely because a `bridge_auth` tag is present.** The HMAC key is intentionally local/private. The daemon uses it for replay/reconciliation. A remote client without an explicit verifier can inspect the tag and request identity but cannot cryptographically authenticate it itself.

Read [references/result-and-recovery.md](references/result-and-recovery.md) before retrying or interpreting partial-success cases.

## Coordinate multiple clients correctly

- One watcher owns mailbox transport, but local edit execution may overlap across **different canonical repositories** when `max_workers > 1`.
- Same-repository mutations remain serialised.
- Queue order is not FIFO. Never make dependencies depend on upload order, filename order, or timestamps.
- If B depends on A, wait for A's successful result, then build B from the resulting commit/state.
- Separate independent work by both transaction ID and branch.
- A local human or another request may advance authoritative state and make a queued request stale. Regenerate from fresh state; do not ask the bridge to silently rebase.

## Handle failure without defeating replay safety

- **Transport download failure before request bytes are obtained:** leave/request remains pending; do not invent an application error.
- **Deterministic protocol/content error:** read the terminal result, fix the request, and submit a **new transaction ID** because the bytes changed.
- **Stale repository/branch base:** materialise again and regenerate the patch from current state under a new transaction ID.
- **Exact crash/retry of the same request:** reuse the same transaction ID only when the canonical request bytes are byte-for-byte equivalent.
- **Push error after successful commit:** preserve/report the local commit as successful local mutation and treat push as a secondary failure.
- **Snapshot error after successful commit:** preserve/report the local commit and materialise later if a snapshot is needed.
- **Result publication ambiguity:** do not resubmit changed work under the same ID. Allow durable local-result recovery/republish to work.

## Respect the execution trust boundary

Configured validation commands are fixed locally, but patched code executed by those commands runs with the local user's privileges. The bridge is **not an OS sandbox**. Do not describe it as one.

Never attempt to bypass rejections involving:

- stale bases;
- tracked-dirty authoritative checkouts;
- unsafe branch names;
- symlink/submodule changes;
- protected secret paths;
- protected CI/automation paths;
- push policy;
- transaction identity/replay conflicts.

Treat those as safety boundaries to resolve at the source, not obstacles to route around.

## Promotion and protected branches

A normal remote transaction ends on a validated safe branch. Merging/promoting to `main` or another protected/default branch is intentionally outside the remote protocol. Use the repository's approved human/local guarded promotion process and independently verify the result afterward.

## Evidence discipline

Distinguish clearly among:

- **requested** — JSON uploaded;
- **accepted/processing** — inferred from pending/metrics, not yet durable success;
- **committed** — result reports a durable local commit;
- **pushed** — result explicitly reports successful push;
- **materialised** — fresh snapshot/result confirms exact head;
- **promoted** — protected branch was changed through an external/local promotion path and independently verified.

Never collapse these into “done”.

## Reference priority

When repository documentation is available, prefer this order when statements conflict:

1. actual current runtime/result/snapshot state;
2. current protocol/security/concurrency documentation at the deployed commit;
3. this skill's bundled references;
4. historical project-state notes.

If the deployed bridge version differs materially from the version assumed by these references, read the deployed `docs/protocol.md`, `docs/security.md`, and `docs/concurrency.md` before sending mutations.

## Bundled references

- [protocol-workflow.md](references/protocol-workflow.md) — exact client workflow and mailbox semantics.
- [examples.md](references/examples.md) — request templates and dependency examples.
- [result-and-recovery.md](references/result-and-recovery.md) — result interpretation, idempotency, retries, and secondary failures.
- [adversarial-audit.md](references/adversarial-audit.md) — traps this skill was audited against and maintenance notes.
