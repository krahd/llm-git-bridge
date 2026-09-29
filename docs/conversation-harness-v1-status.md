# Conversation Harness v1 — implementation status

Status: ACTIVE — isolated parallel implementation
Date: 2026-09-28
Workspace job: `conversation-harness-v1-20260928`
Resource: `harness/v1`
Base: `2437e518f8b8556f0baff72fbadd4b57afb23165`

## Safety boundary

The existing ChatGPT Shell Bridge v5 is live and in use. Harness v1 must coexist with it and must not restart, replace, reconfigure, deploy over, or mutate its installed runtime. All development occurs in this isolated workspace. Runtime state, sockets, launch labels, locks, browser/native identifiers and job namespace are separate.

## Current phase

Implement the transport-independent job/lease/handoff state machine and SQLite persistence, then local Unix-socket/CLI protocol. Safari integration follows only after the core passes focused tests.

## In-flight operation

none

## Exact next action

Create `src/llm_git_bridge/harness/` core modules and focused tests. Success: state transitions, lease fencing, idempotent handoffs, expiry/reclaim, and SQLite crash/reopen semantics pass without touching the live bridge runtime.
