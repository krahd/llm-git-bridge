# Trusted operations for Local Executor Bridge v6

Status: **source dispatcher candidate under qualification**. The request path exists on the feature branch, but is neither installed nor accepted on the user's Mac. No trusted helper is enabled without an administrator-installed registry entry.

## Intent

A trusted operation is an administrator-installed, named, narrowly scoped executable. It is **not** arbitrary agent-provided shell, a user-selected executable path, or a blanket exception to the macOS sandbox. The GitHub connector remains the default for repository read/write operations; the Mac executor is reserved for genuinely local work.

## Required admission / execution semantics

1. Privileged local provisioning installs the helper in an owner-controlled immutable installation directory, registers its exact SHA-256, allowed action names, and resource roots. An agent must never be able to change the registry or nominate a new executable.
2. Incoming requests specify only a registered operation name, an allowlisted action, and schema-validated arguments. The bridge resolves the record from its own trusted local config rather than from the request payload. Unknown actions fail closed.
3. The bridge checks the helper’s on-disk identity and guarantees that the binary actually executed is the verified version, with no replacement race. The `verify_installed_helper` preflight alone is insufficient to prove this.
4. The bridge displays the operation/action, target resources, expected external effect, reversibility, and the exact underlying command through its authenticated/click-only operator channel. Approval is bound to the normalized request digest and expires. Agent-authored explanations are untrusted.
5. Any out-of-repo secret access is granted to that helper only, never the ordinary shell worker. The helper itself constrains target hosts, credentials, file paths, and remote effects.
6. A durable logical-operation ID governs request admission before any side effect. An ambiguous result triggers reconciliation, not automatic replay. A recovery helper must expose inspect/prepare/commit/rollback stages and evidence where relevant.
7. Read-only operations inside the existing sandbox require no privileged helper and no approval. Irreversible or external mutations need an informed confirmation; blanket approval prompts are not acceptable.

## Acceptance gates

- Malformed/unregistered operation and unlisted action rejected before execution.
- Symlink, path traversal, group-writable or digest-mismatched helper rejected.
- TOCTOU-safe launch path, argument schema, independent approved operator decision, rejection, defer/expiry and audit receipt demonstrated on macOS.
- Positive and negative staging tests for narrowly scoped secret/SSH access, with unrelated home secrets still denied.
- Lost-result recovery, process containment, no duplicate execution, mailbox exclusivity and fail-closed rollback validated.
- Staged build/source identity matches canonical GitHub; production v5 continues until all cutover gates pass.
- After verified production acceptance, retire old consumers and staging, retain rollback evidence, and independently verify one production executor.

Deployment-specific logic and credentials do not belong in this bridge repository. The website release itself remains outside this work.


## Source interface (not a raw shell)

A trusted request contains `protocol`, `id`, a repository `cwd`, non-empty `explanation`, stable `operation_id`, and:

```json
"trusted_operation": {"name": "registered-helper-name", "action": "inspect"}
```

Do not include `command`, `stdin_b64`, or elevated `write_scope`. The bridge rejects any such mixture. An unregistered name is rejected before STARTED; no agent may supply an executable or override a resource root.

In the trusted local bridge configuration, the administrator registers `trusted_operations.<name>` with exactly `executable`, `executable_sha256`, `permitted_roots`, and `permitted_actions`. The helper must be outside the agent-writable repository root. The bridge checks its bytes before approval and again before executing.

After a positive operator decision, the direct process argv contract is `[pinned-executable, action, *registered-roots]`, never an agent-supplied command string. The registered helper must separately enforce its own target and credential restrictions.

The source-level integrity and approval logic does not prove TOCTOU-safe launch or secure installation ownership. Both remain live release acceptance requirements. No production credentials or website deployment operations are registered by this change.
