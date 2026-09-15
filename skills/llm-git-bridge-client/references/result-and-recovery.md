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

## Publication ambiguity

If result upload times out or transport fails after the local result is durable, allow the daemon's recovery logic to reconcile/republish. Avoid duplicate changed requests. In concurrent mode, post-durable publication failure must not be interpreted by the client as permission to re-execute the mutation.

## Promotion boundary

A safe-branch transaction result does not prove protected `main` changed. Protected-branch promotion is a separate local/human operation. After promotion, independently materialise or otherwise verify the protected branch before reporting it as promoted.
