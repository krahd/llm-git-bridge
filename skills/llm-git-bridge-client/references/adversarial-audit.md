# Adversarial audit of this skill

This file records the failure modes the skill must continue to resist when maintained.

## Audit cases and required behaviour

1. **Stale documentation conflict** — If general usage prose contradicts the deployed protocol/security/concurrency docs or runtime state, prefer runtime and version-matched authoritative docs. RC4's older usage text incorrectly implied all edits remain globally serial; RC4 concurrency allows different repositories to overlap while same-repository mutation stays serial.
2. **`branch: main` materialisation mistake** — Current checkout materialisation must omit `branch`; explicit branch materialisation is for validated safe-prefix branches.
3. **FIFO dependency assumption** — Reject upload/filename/time ordering as a dependency mechanism. Wait for the prior terminal result.
4. **Transaction-ID reuse** — Reuse only for byte-identical retry/recovery. Changed content requires a fresh ID.
5. **Duplicate Drive filenames** — Treat global unique transaction IDs as mandatory because Drive permits duplicate names.
6. **Inbox-presence misdiagnosis** — A visible request can be actively processing or awaiting cleanup. Do not resubmit solely because it remains present.
7. **HMAC overclaim** — A remote client cannot independently verify `bridge_auth` without the private local key.
8. **Partial-success collapse** — Preserve durable commit success when push or snapshot publication fails secondarily.
9. **Protected-branch overreach** — Remote protocol does not merge/promote to `main`; do not fabricate a transaction that bypasses local promotion policy.
10. **Arbitrary-shell temptation** — `run` contains local symbolic names only. Never place argv/shell text in a request.
11. **Sandbox overclaim** — Configured commands can execute patched code with local-user privileges. The bridge is not an OS sandbox.
12. **Safety-boundary bypass** — Do not route around stale base, tracked dirt, protected paths, symlink/submodule, push-policy, or replay-identity rejections.
13. **Concurrency overclaim** — One watcher owns transport; only local execution across different canonical repositories may overlap. Same repository remains serial.
14. **Premature completion** — Distinguish request upload, commit, push, materialisation, and protected-branch promotion as separate evidence states.
15. **Version drift** — If runtime version differs from the bundled assumptions, read the deployed protocol/security/concurrency docs before mutation.
16. **Manual-registration assumption** — Do not tell the operator to `scan` for every repository. Repositories beneath approved roots are auto-discovered; adding a new root remains local policy.
17. **Repository-ID privilege inheritance** — If a repository is locally replaced and receives a new ID, never assume configured commands or push permission follow its name/path. Re-read the public registry.

## Audit outcome for initial version

PASS after repairs. The skill was rewritten to address every case above, and its source hierarchy was changed to avoid inheriting the stale RC4 `docs/usage.md` concurrency sentence.

## Maintenance rule

On every bridge protocol/security/concurrency release, rerun these cases against the new deployed docs and update the skill in the same commit or explicitly record why no change is needed.
