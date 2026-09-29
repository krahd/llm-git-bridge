# Conversation Harness v1 — implementation status

Status: CORE/RUNTIME COMPLETE — Safari app build blocked by local Xcode/CoreDevice mismatch
Date: 2026-09-28

## Verified complete

- Harness core/state machine, SQLite persistence, fencing, handoffs, operation identity, request journalling, Unix-socket daemon, CLI, browser pairing API, and Safari WebExtension source are implemented.
- Canonical repository integration and independent GitHub remote verification passed.
- Harness-focused suite passed 27/27; full repository suite passed 357/357; compileall and git diff --check passed.
- The parallel harness LaunchAgent is active under `net.laurenzo.chatgpt-conversation-harness-v1`, with state and runtime paths separate from Shell Bridge v5.
- Live Shell Bridge v5 remained at the same publisher PID during harness activation. Both services are supported to coexist indefinitely; migration of existing conversations is not required.
- Safari WebExtension packaging produced a generated Xcode project from canonical source.

## Browser transport implementation variance

The original architecture plan selected Safari native messaging. The implementation spike superseded that choice: Safari v1 uses an authenticated loopback API bound to `127.0.0.1:47653`, plus the user-only Unix socket for CLI/service traffic. The extension requests only `chatgpt.com` and the exact loopback origin, stores only a bearer token obtained from a short-lived one-time pairing code, and does not auto-submit prompts. `safari/README.md` is authoritative for this implemented browser boundary. Native-messaging sections in the historical architecture plan are therefore superseded.

## External blocker

The generated Safari containing app cannot currently be compiled because the installed Xcode fails while loading `DVTCoreDeviceCore`: its framework expects a `CoreDevice` symbol not present in the installed private framework. This is a local Xcode/CoreDevice installation mismatch, not a harness source failure. The packaging script itself completed before `xcodebuild` failed. Repairing/reinstalling Xcode or the corresponding macOS developer components is intentionally outside this task while the Mac is in active use.

## Remaining acceptance gate

After Xcode is repaired: build/run the generated containing app, enable Conversation Harness in Safari, pair it with `llm-git-harness pair`, and run the manual new-chat/handoff pilot. Until then, browser automation is not claimed as production-accepted. The daemon/CLI continuity path is usable independently.


## Stale-socket restart recovery

A deployment restart exposed a stale Unix-socket failure: launchd correctly restarted the harness process, but the daemon refused the socket pathname left by the prior process. The daemon now reclaims only a socket that is provably stale (connect returns ECONNREFUSED), preserves a live socket, fails closed on non-socket paths or unverifiable errors, and removes only the exact socket inode it created. Regression tests cover stale recovery, live-socket preservation, non-socket preservation, and normal cleanup.


## Recovery and terminal-state invariants

The runtime now enforces the audited recovery gate: a job with a `started` or `indeterminate` external operation may be handed off and claimed, but no new semantic mutation or new external operation may begin until that operation is reconciled. `indeterminate` operations can be resolved explicitly to `completed` or `failed`. Terminal job lifecycles (`completed`, `failed`, `cancelled`) are immutable and cannot be re-leased; lifecycle transitions are checked against the documented state machine. Regression tests cover handoff with in-flight work, indeterminate reconciliation, terminal immutability, and illegal transitions.
