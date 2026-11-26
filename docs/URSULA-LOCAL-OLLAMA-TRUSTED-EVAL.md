# Ursula local-Qwen tests via the pinned Mac bridge

**Status:** source implementation prepared, not yet enrolled or accepted on the operator's Mac. Never claim remote Ollama inference is working until the native approval and first live result have both been observed.

## Why a trusted action is necessary

Local Executor Bridge v6 intentionally blocks raw repository shell commands from contacting localhost, and an explicit `system` request cannot elevate a safe shell command. The installed v6 build supports owner-enrolled `trusted_operation` descriptors: immutable helper path/SHA, fixed action names, permitted roots, and mandatory native click-only approval per invocation.

This capability does **not** modify the v6 daemon, permit generic remote shell, or allow an agent to enroll a helper. Only the operator's **interactive Terminal** may provision it.

## Enrollment (one time, outside the mailbox)

1. Review `shell_bridge/ursula_ollama_eval_helper.py`, `shell_bridge/enrol_ursula_ollama_eval.py`, and the changes to Ursula's live evaluation runner. Both canonical repositories should have their validated changes on `main`.
2. In a local interactive Terminal, update a **clean** checkout of `krahd/llm-git-bridge`; from that checkout run:
   ```bash
   python3 shell_bridge/enrol_ursula_ollama_eval.py
   python3 shell_bridge/enrol_ursula_ollama_eval.py --apply
   ```
   The first call shows the intended operation without mutating anything. The second explicitly enrolls and restarts the installed v6 launch agent. The installer refuses a missing/malformed installation, unverified build, active/unfinished requests, existing conflicting helper registration, symlinks, and disabled native approvals. It preserves a local configuration rollback copy.
3. Verify the bridge's next heartbeat, then submit an `ollama-status` trusted request. The owner must click **Allow** in the native approval queue. Inspect the terminal result and request digest.

If this qualification fails, keep the bridge sandbox unchanged and return to operator-local Terminal runs; do not work around it with a system-scope raw command or daemon self-upgrade.

## Exact trusted request contract

For each action, submit raw UTF-8 `<id>.json` to the **already pinned** v6 request folder (never use historical mailbox IDs):

```json
{
  "protocol": 1,
  "id": "ursula-core-c3-unique-run-id",
  "operation_id": "ursula-core-c3-unique-operation-id",
  "cwd": "/Users/tom/tom-repos/projects/ursula/apps/storytelling/prototype-0",
  "trusted_operation": {
    "name": "ursula-ollama-eval",
    "action": "core-c3"
  },
  "explanation": "Run the exact three-turn local Qwen core C study using the administrator-pinned helper, keeping the report in ignored local data. Native approval required.",
  "timeout_seconds": 290
}
```

No `command`, `stdin_b64`, model string, output path, or `write_scope` may be supplied. `id` and `operation_id` must be unique per new run. Avoid replaying an indeterminate operation.

Owner-pinned action set:

| Action | Effect |
|---|---|
| `ollama-status` | Check that local Ollama responds and the expected Qwen tag is installed |
| `core-c3` | Run C-only, 3-turn core scenario |
| `core-abc2` | Run A/B/C, 2-turn core scenario |
| `social-c3` | Run C-only, 3-turn social scenario |

All study actions use `qwen3:30b-a3b-instruct-2507-q4_K_M`, a 16,384-token context, and an isolated output under `data/local/evals/`. Tests never overwrite prior evaluations. Each helper invocation is limited to 250 seconds, inside the bridge's maximum 300-second request deadline.

## Extra restrictions inside the trusted helper

- Validates the clean tracked checkout and canonical Ursula origin; it cannot execute an arbitrary repository or choose an arbitrary executable/model.
- Removes inherited credentials and environment variables, and launches npm with a fixed argv (never a shell string).
- Requires macOS `sandbox-exec` (no unsandboxed fallback), denies home-directory reads outside the Ursula repository (the web status endpoint requires read-only access to its parent `.git`), and denies filesystem writes outside the ignored `dist/` and `data/local/evals/` trees.
- Denies arbitrary network connections, permitting only localhost Ollama TCP/11434 and the fixed local Ursula studio TCP/41731; the studio's port is pinned by `URSULA_STUDY_WEB_PORT`. These local endpoints are still trusted services and must be regarded as a bounded security exposure.
- The native approval UI will display the registered helper's pinned digest and action. The helper never returns arbitrary secrets or credentials; study results stay local and are inspected afterwards using an ordinary read-only bridge request.

## Qualification gates before declaring live

1. Offline unit tests of the helper and enrollment script pass.
2. GitHub CI for the Ursula fixed-port runner passes.
3. Operator reviews and performs the one-time enrollment locally, out of band.
4. Approved `ollama-status` request completes with expected model.
5. Approved `core-c3` request completes, or fails with a preserved report and useful diagnostics, and the output is verified via a **separate** read-only bridge request.
6. Confirm no writes beyond intended ignored paths, no network outside localhost allowlist, and existing experiments remain untouched.

macOS sandbox profiles can fail at runtime even when syntactically plausible. A nested sandbox probe through the existing read-only v6 shell returned `sandbox_apply: Operation not permitted`, which does not establish whether this helper's profile works when launched directly after native approval. Treat Mac acceptance as a remaining blocker, not a passing test.
