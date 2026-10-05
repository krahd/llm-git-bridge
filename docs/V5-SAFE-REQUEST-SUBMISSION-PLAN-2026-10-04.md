# v5 safe request submission — final plan (2026-10-04)

## Goal

Reduce ChatGPT-to-Drive request submission failures without changing production v5 execution authority, bypassing platform safety controls, introducing a connector/plugin, or touching v6.

## Evidence and corrected conclusions

Observed successful submissions include small repository reads, reads outside the repository, `launchctl print`, and a harmless `system` request that reached v5 and then waited for local approval. Therefore request size, `launchctl`, outside-repository reads, and `system` scope are not categorical platform blockers.

Observed failures separate into three classes:

1. platform upload rejection before Drive;
2. malformed/schema-invalid requests that reach v5 and are rejected;
3. bridge/runtime failures after acceptance.

The production popup hotfix also exposed a deployment lesson: installed bytes alone are not proof that the live daemon is running them. Runtime acceptance must include a new PID/heartbeat or a capability probe unique to the new build.

## Adversarial design decision

An experimental direct-`argv` extension to the v5 daemon was rejected from the final design. It increased protocol/runtime surface and exposed a classification edge case for interpreter-wrapped commands. The transport reliability problem does not require a daemon protocol change.

Keep production v5 unchanged. Author literal argv locally, validate it, render it with `shlex.join`, and emit the existing `command` request field.

Reject argv authoring forms that embed code inside interpreters (`sh`/`zsh`/`bash -c`, `python -c`, `node -e`, etc.) or obscure the executable with `env`. Such work belongs in reviewed repository-side helpers invoked by a short request.

## Request-construction policy

1. Every request has a unique ID and concise non-empty `explanation`.
2. Prefer the deterministic request-authoring helper for one executable plus literal arguments.
3. The helper emits the existing production-v5 `command` schema; it does not emit `argv` to the daemon.
4. Use raw shell commands only with explicit opt-in when shell semantics are genuinely needed.
5. Treat pipelines, chaining, redirection, substitutions, heredocs, and multiline scripts as signals to move logic into reviewed repo-side helpers.
6. Keep one semantic purpose per request.
7. Keep `write_scope` truthful and minimal; never relabel or obscure work to influence platform or local safety classification.
8. Do not retry identical bytes in loops after platform rejection. Simplify the request shape or move complex logic to reviewed repository code.
9. A matching durable result is authoritative; request-folder visibility may lag cleanup.
10. Distinguish platform upload rejection, bridge rejection, command failure, and ChatGPT delivery timeout.

## Artifact-transfer fallback for repository edits

When the platform rejects a request because it contains inline patch/edit code, do not encode or disguise the mutation in the request JSON. A transparent fallback is:

- upload the reviewed patch/replacement file as a named Drive artifact;
- use a small explicit repository-scoped request to copy that artifact into an isolated worktree;
- validate the artifact/diff in the worktree;
- commit only the intended files and verify the remote ref.

This keeps source changes reviewable while keeping request JSON simple.

## Acceptance gates

- authoring helper tests pass;
- legacy v5 tests remain unchanged/passing;
- no production daemon protocol change;
- no v6 files/services changed;
- skill documentation matches the deployed production-v5 schema;
- Git diff is limited to authoring helper/tests/docs/skill source;
- branch push is independently verified;
- live v5 health remains current with zero unexplained STARTED-without-FINISHED requests.
