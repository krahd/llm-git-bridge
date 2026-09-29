# Conversation Harness v1 — implementation status

Status: COMPLETE — integrated and remotely verified
Date: 2026-09-28

## Safety boundary

The existing ChatGPT Shell Bridge v5 remains live, separate, and supported. Harness v1 coexists with it; no migration of existing conversations is required. The harness uses a distinct runtime namespace, state directory, socket, launch label, browser/native identifiers, and job namespace.

## Verified implementation

- durable SQLite job state with independent lifecycle/version/lease state;
- fenced leases with expiry and safe reclaim;
- atomic handoff/claim semantics and continuation projections;
- idempotent external-operation records;
- at-most-once local request journalling with indeterminate replay handling;
- Unix-domain-socket daemon and CLI protocol;
- browser pairing/pending-prompt API with narrow origin/token checks;
- Safari/WebExtension packaging and persistence tests;
- separate, stage-only installer/activation path for the harness runtime;
- coexistence with the existing Shell Bridge v5 as a permanent supported topology.

## Acceptance evidence

Canonical integration commit before this status-only closure: `a1713cc7c8feac959aacd6b2e40b978ad3e79337`. At acceptance, local `HEAD`, `origin/main`, and `git ls-remote origin refs/heads/main` all matched that commit. Harness-focused tests passed; the full repository test suite passed; `compileall` and `git diff --check` passed.

The live Shell Bridge was not replaced, restarted, reconfigured, or migrated as part of Harness v1 implementation. Existing conversations may continue using it indefinitely; Harness v1 is opt-in.

## Required next action

None for v1 implementation. Real-world opt-in trials may now be run against the separately staged harness. Stage 2 / Cloudflare remains optional future work and must not change the v1 authority or replay semantics without a new adversarial review.
