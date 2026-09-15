# Request examples

Replace example IDs, repository names, SHAs, branches, patches, and command names with fresh values from current state.

## Materialise authoritative checkout

```json
{
  "protocol": 2,
  "kind": "materialize",
  "transaction_id": "tx-materialize-project-20260914-a1b2",
  "repo": "my-repo"
}
```

Do not add `"branch": "main"` merely to read the current checkout; explicit branch materialisation is limited to safe-prefix branches.

## Materialise an existing bridge branch

```json
{
  "protocol": 2,
  "kind": "materialize",
  "transaction_id": "tx-materialize-branch-20260914-c3d4",
  "repo": "my-repo",
  "branch": "ai/feature-x"
}
```

## Doctor

```json
{
  "protocol": 2,
  "kind": "doctor",
  "transaction_id": "tx-doctor-20260914-e5f6"
}
```

## Diagnostics

```json
{
  "protocol": 2,
  "kind": "diagnostics",
  "transaction_id": "tx-diagnostics-20260914-g7h8",
  "limit": 20
}
```

`limit` defaults to 10 and is bounded to 1–50.

## Edit transaction

```json
{
  "protocol": 2,
  "kind": "transaction",
  "transaction_id": "tx-doc-fix-20260914-j9k0",
  "repo": "my-repo",
  "base_sha": "0123456789abcdef0123456789abcdef01234567",
  "branch": "ai/doc-fix",
  "patch": "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-old\n+new\n",
  "run": ["test"],
  "commit_message": "Correct documentation",
  "push": true,
  "publish_snapshot": false
}
```

`push: true` succeeds only when the operator has independently enabled push for that repository.

## Dependent edits

Correct:

1. Submit A.
2. Wait for A success and read commit `C1`.
3. Materialise A's branch if content confirmation is needed.
4. Build B against `C1`.
5. Submit B with a new transaction ID.

Incorrect:

- upload A and B together and assume alphabetical filename or upload order makes A execute first;
- reuse A's transaction ID for B;
- keep B's old base and expect auto-rebase.

## Exact retry after uncertain acknowledgement

Only when request bytes are identical:

- same transaction ID;
- same canonical JSON request content;
- same patch/base/branch/run/message/flags.

If any request field changes, generate a new transaction ID.
