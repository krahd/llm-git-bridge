# v5 safe request submission plan — 2026-10-04

## Scope

Improve reliability of ChatGPT -> Google Drive -> production v5 request submission without changing the bridge trust boundary, bypassing platform safety controls, touching v6, or replacing Drive transport.

## Observed failures

Two failure classes were previously conflated:

1. **Platform submission failure**: ChatGPT's Drive upload is blocked before the request reaches the Mac.
2. **Bridge validation/execution failure**: the request reaches v5 and is rejected, sandboxed, approved, executed, or timed out according to v5 semantics.

A missing `explanation` reached v5 and was correctly rejected. Therefore a bridge rejection is not evidence of a platform upload failure.

## Feasibility evidence

A benign four-case matrix was submitted through the same Drive connector:

- small repository read: accepted and completed;
- read of the installed bridge file outside the repo: accepted and completed;
- `launchctl print` read: accepted and completed;
- harmless `system` request (`true`): accepted by the platform and reached the bridge; it later timed out waiting for operator approval.

Therefore neither request size, `launchctl`, outside-repository reads, nor `system` scope alone explains the platform blocks.

Source inspection established that the repaired v5 source already supports mutually exclusive `command` and `argv` request forms. For `argv`, v5:

- validates a non-empty string array;
- renders it with `shlex.join` for logging/classification/approval display;
- executes it directly with `subprocess.Popen`, without `/bin/zsh -lc`;
- applies the same write-plan, high-impact classifier, sandbox, and operator-confirmation path as shell commands.

This makes `argv` a feasible compatibility-preserving way to remove unnecessary shell quoting, pipelines, heredocs, command substitution, and chaining from routine requests.

## Adversarial review and corrections

### Rejected idea: infer the platform filter

We cannot observe or control the upstream safety classifier, and the accepted matrix disproves simple rules such as "system requests are blocked" or "launchctl is blocked". The implementation must not attempt to evade, encode around, or reverse-engineer platform safety checks.

### Rejected idea: add a new connector/plugin

A first-class local-execution connector would defeat the reason the bridge exists and is not an available capability.

### Rejected idea: automatically rewrite arbitrary shell to argv

`shlex.split` is not semantics-preserving for globbing, environment expansion, redirection, pipelines, command substitution, shell builtins, or compound expressions. The authoring layer must recommend, not silently transform.

### Rejected idea: hide complex work in base64 or opaque payloads

Encoding does not make work safer and can make review worse. Complex logic should live in reviewed repository scripts and be invoked with a short explicit argv request.

### Added requirement from the hotfix incident: verify live capability, not only installed bytes

The popup hotfix replaced the installed file but the old daemon remained alive. File hash equality is therefore insufficient deployment evidence. Any production deployment must prove the running daemon has changed or exercise a capability unique to the new build.

## Final request-construction policy

1. Every request has a unique ID and a concise non-empty `explanation`.
2. Prefer `argv` whenever the intended operation is one executable plus literal arguments.
3. Use `command` only when shell semantics are genuinely required.
4. Never combine unrelated phases into one shell command merely to reduce tool calls.
5. If shell complexity includes chaining, pipelines, redirection, command substitution, heredocs, or multiline scripts, prefer a reviewed repo-side helper invoked with `argv`.
6. Do not obscure commands to influence platform safety classification.
7. Keep mutation scope truthful. `argv` never changes the required `write_scope` or approval category.
8. Treat result publication as authoritative. Request-folder presence can lag and is not proof that work remains pending.
9. On platform upload rejection, do not replay the same payload blindly. Re-express the same intent in the simplest truthful request shape, or move complex logic into reviewed repository code.
10. For substantial repository work, continue to use the workspace coordinator; safe request shape is transport hygiene, not a replacement for repository concurrency control.

## Implementation

Add `shell_bridge.request_authoring` with:

- deterministic argv/request validation;
- request builder that requires explicit opt-in for shell commands;
- shell-complexity analysis for linting only;
- policy recommendation: `preferred_argv`, `rewrite_as_argv`, or `move_to_reviewed_helper`;
- CLI `build` and `lint` modes for tests, maintenance, and reproducible examples.

Add tests that prove:

- argv requests are emitted without a shell command;
- shell use requires explicit opt-in in the authoring API;
- simple shell strings are flagged for argv conversion;
- compound shell is flagged for a reviewed helper;
- existing v5 validation accepts authored argv and records `execution_mode=argv`;
- the rendered argv still passes through the high-impact classifier.

## Acceptance gates

- new authoring tests pass;
- existing focused v5 tests pass;
- full relevant test suite passes or any pre-existing unrelated failures are explicitly separated;
- `git diff --check` passes;
- only v5 maintenance branch changes;
- branch is pushed and remote ref independently verified;
- production bridge is not modified by this change unless separately deployed and runtime-verified.

## Second adversarial audit notes

The exact v5 validator constants are: lowercase request IDs, maximum 1024 argv items, maximum 256 KiB rendered command text, maximum 1 MiB decoded stdin, maximum 4096-byte explanation, and maximum 300-second command timeout. The authoring layer mirrors those limits.

Running the entire legacy suite from a `read_only` bridge request is not a valid regression environment for tests that themselves invoke `sandbox-exec`: macOS rejects nested sandbox application with exit 71. Those failures are execution-environment artifacts, not changes caused by this three-file patch. Focused authoring tests remain runnable under read-only scope; sandbox integration tests require an unsandboxed controlled test invocation.
