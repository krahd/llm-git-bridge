# Result, recovery, and retry reference

## Durable acknowledgement

The bridge publishes `v2/results/<transaction-id>.json`. The daemon creates the signed result locally before remote publication and uses durable local/Git state for replay recovery.

The original inbox request is not proof of processing state. It may remain visible until result publication and cleanup succeed.

## Remote HMAC nuance

Results include a `bridge_auth` HMAC tag. The secret key remains local/private to the bridge. This protects daemon replay/reconciliation from forged mailbox results.

A remote LLM that only sees Drive contents does **not** possess the key and therefore cannot independently verify the HMAC. It must not say “HMAC verified” unless a trusted verifier actually performed that check. It can still verify visible consistency such as transaction ID, request hash when exposed, branch/commit identities, and later materialised state.

## Terminal errors

A deterministic invalid request produces a signed terminal error result once request bytes were successfully downloaded. Correct the defect and use a new transaction ID because the request bytes changed.

A transport failure before request bytes are obtained is different: no validated application request exists yet, so the request stays pending for retry and no terminal application error should be invented.

## Stale base

For a stale repository or branch base:

1. materialise current state;
2. inspect the new HEAD/branch tip;
3. regenerate the patch;
4. submit as a new transaction.

Do not attempt to bypass stale checks or ask the bridge to auto-rebase.

## Crash-safe exact retry

Git bridge commits carry reserved identity metadata bound to both transaction ID and SHA-256 of the canonical request. The daemon can recover an exact already-created commit after a crash without rerunning patch/tests.

Therefore:

- byte-identical retry may reuse the same transaction ID;
- changed bytes under the same transaction ID are a conflict and must be rejected;
- do not “fix” a timed-out request in place using the same ID.

## Secondary push failure

Push occurs after the durable local commit. A result can therefore represent:

- transaction/commit success;
- `push.status: error`.

Report this as “local commit succeeded; push failed”, not “transaction failed with no commit”. The next action depends on local policy and remote state, not reapplying the patch blindly.

## Secondary snapshot failure

Snapshot generation/publication is also secondary to the durable commit. By default edit results defer snapshot publication. When exact branch contents are needed later, issue a materialisation request.

## Materialisation acknowledgement ambiguity

A read-only materialisation can fail closed with an error such as `rclone rc write outcome is unknown; retry required` when the transport cannot determine whether snapshot publication was acknowledged. This error does **not** by itself prove that the snapshot write failed or that Git state is wrong.

Use this bounded recovery procedure:

1. Do not immediately submit repeated materialisations.
2. Inspect the authoritative remote snapshot path (`v2/repos/<repo-id>/snapshot.json`, or the safe-branch snapshot path when applicable).
3. Compare its `branch`, `head`, generation time, file count/hashes as needed against independent authoritative evidence such as the current registry head, a durable edit result, or a previously verified protected-branch state.
4. If the snapshot already contains the required current branch/head and bytes, use it as materialised-state evidence and record the transport acknowledgement ambiguity separately.
5. If the snapshot is stale or absent and fresh bytes remain necessary, confirm no earlier materialisation is still pending, then issue **one** fresh materialisation under a new globally unique transaction ID.
6. If the fresh attempt returns the same acknowledgement ambiguity, stop retrying automatically. Report a transport residual and use other independent evidence where sufficient; escalate only if fresh snapshot bytes are genuinely required and unavailable.

Materialisation is read-only with respect to Git state, so this recovery rule never converts an ambiguous edit/mutation into assumed success.

## Publication ambiguity

If result upload times out or transport fails after the local result is durable, allow the daemon's recovery logic to reconcile/republish. Avoid duplicate changed requests. In concurrent mode, post-durable publication failure must not be interpreted by the client as permission to re-execute the mutation.

## Promotion boundary

A safe-branch transaction result does not prove protected `main` changed. Protected-branch promotion is a separate local/human operation. After promotion, independently materialise or otherwise verify the protected branch before reporting it as promoted.

When constructing a local promotion/canary/release wrapper, preserve evidence and exit semantics:

- preflight must not mutate production state;
- cleanup/restart/restore must be conditional on the corresponding mutation actually occurring;
- capture child exit status immediately before any other command can replace `$?`;
- cleanup traps must preserve and return that captured status;
- verify protected refs/runtime independently after the wrapper completes.
