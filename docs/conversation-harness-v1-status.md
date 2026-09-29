# Conversation Harness v1 — implementation status

Status: DEPLOYED — daemon/CLI and live handoff acceptance passed; manual Safari/ChatGPT pilot pending
Date: 2026-09-28

## Verified complete

- Harness core/state machine, SQLite persistence, fencing, handoffs, operation identity, request journalling, Unix-socket daemon, CLI, browser pairing API, and Safari WebExtension source are implemented.
- Canonical repository integration and independent GitHub remote verification passed.
- Harness-focused acceptance passed; the latest temporary-extension validation ran the full repository suite at 366/366, and git diff --check passed.
- The parallel harness LaunchAgent is active under `net.laurenzo.chatgpt-conversation-harness-v1`, with state and runtime paths separate from Shell Bridge v5.
- Live Shell Bridge v5 remained at the same publisher PID during harness activation. Both services are supported to coexist indefinitely; migration of existing conversations is not required.
- Safari WebExtension packaging produced a generated Xcode project from canonical source.

## Browser transport implementation variance

The original architecture plan selected Safari native messaging. The implementation spike superseded that choice: Safari v1 uses an authenticated loopback API bound to `127.0.0.1:47653`, plus the user-only Unix socket for CLI/service traffic. The extension requests only `chatgpt.com` and the exact loopback origin, stores only a bearer token obtained from a short-lived one-time pairing code, and does not auto-submit prompts. `safari/README.md` is authoritative for this implemented browser boundary. Native-messaging sections in the historical architecture plan are therefore superseded.

## Live deployment acceptance — 2026-09-28

- Canonical `origin/main` and the installed harness runtime were verified at `2ca74c9e36cd4565e71cb989971fb984cffb8e9d`.
- The harness-only deployment restarted `net.laurenzo.chatgpt-conversation-harness-v1` while the live Shell Bridge v5 publisher remained PID `38449`; the two runtimes remained independent.
- Installed Unix-socket `ping` passed and the authenticated loopback service returned HTTP 200 on `127.0.0.1:47653`.
- The staged Safari extension contains five files and byte-matches canonical `safari/extension/`.
- A live continuity pilot exercised the installed daemon: logical conversation A created/acquired/handed off a job; the client pilot then stopped after the durable handoff; recovery inspected the existing pending handoff instead of replaying it; logical conversation B claimed it; the event log showed `job_created`, `lease_acquired`, `handoff_created`, and `handoff_claimed`; the final lease was released.
- This validates daemon/CLI continuation and recovery against the deployed runtime, including the intended missing-delta recovery discipline after a client-side failure.

## Direct live A-to-B handoff acceptance — 2026-09-28

- The harness was redeployed from canonical `origin/main` at `2a67844a374f22256375003ed7237fa0995e2df5`; installed `ping` passed and the canonical Safari extension was staged at `~/.local/share/chatgpt-conversation-harness-v1/safari-extension/`.
- The harness-only redeploy left the live Shell Bridge v5 publisher on the same PID (`38449`) during the acceptance transaction; the services remained independent.
- Live job `selftest-handoff-20260929-0523` exercised the running Unix-socket daemon: actor A acquired generation 1, created a handoff, actor B claimed it at generation 2, the continuation projection exposed the claimed handoff and new lease, and the event log recorded `job_created`, `lease_acquired`, `handoff_created`, and `handoff_claimed`.
- The self-test then transitioned the job to terminal `completed`, leaving no actionable pilot job behind.
- The first client attempt intentionally became a recovery exercise: a flat lease object was rejected with `validation_error` because mutating lease-bound protocol calls require the token under `args.lease`. The same durable job was inspected and resumed with a new request ID and the correct lease envelope; no completed mutation was replayed. This confirms fail-closed protocol validation and the intended missing-delta recovery discipline in the deployed runtime.

## Browser pilot path

The local Xcode/CoreDevice installation still cannot build the generated Safari containing app, but this no longer blocks v1 browser acceptance. Safari 18.4 and later can temporarily install a WebExtension directly from a folder for development. The harness installer therefore stages the canonical extension at `~/.local/share/chatgpt-conversation-harness-v1/safari-extension/`; Xcode is required only for durable containing-app packaging/distribution.

## Remaining acceptance gate

Load the staged directory with Safari's **Add Temporary Extension…** developer control, pair it with `llm-git-harness pair`, and run the manual new-chat/handoff pilot. Browser automation is production-accepted only after that real Safari/ChatGPT pilot passes. The daemon/CLI continuity path remains independently usable.


## Stale-socket restart recovery

A deployment restart exposed a stale Unix-socket failure: launchd correctly restarted the harness process, but the daemon refused the socket pathname left by the prior process. The daemon now reclaims only a socket that is provably stale (connect returns ECONNREFUSED), preserves a live socket, fails closed on non-socket paths or unverifiable errors, and removes only the exact socket inode it created. Regression tests cover stale recovery, live-socket preservation, non-socket preservation, and normal cleanup.


## Recovery and terminal-state invariants

The runtime now enforces the audited recovery gate: a job with a `started` or `indeterminate` external operation may be handed off and claimed, but no new semantic mutation or new external operation may begin until that operation is reconciled. `indeterminate` operations can be resolved explicitly to `completed` or `failed`. Terminal job lifecycles (`completed`, `failed`, `cancelled`) are immutable and cannot be re-leased; lifecycle transitions are checked against the documented state machine. Regression tests cover handoff with in-flight work, indeterminate reconciliation, terminal immutability, and illegal transitions.
