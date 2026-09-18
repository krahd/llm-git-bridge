# RepoReach product and managed-service strategy

Status: superseding design checkpoint
Date: 2026-09-18

This document supersedes the 2026-09-17 product checkpoint. The implementation remains internally named llm-git-bridge while the independent Git-parity roadmap is active; the public product identity is RepoReach (RR) and the hosted relay is RepoReach Relay (RRR).

## 1. Product thesis

RepoReach gives AI systems controlled access to Git repositories wherever those repositories live.

It is not a generic GitHub connector and not a general remote-computer agent. Its durable value is the combination of authoritative local/private Git, provider-neutral AI clients, an RRR-first connection experience over a transport-neutral core, exact-base transactions, local validation and policy, durable replay/recovery, path-free repository identity, and controlled commit/push authority.

Google Drive remains a useful proven transport, not the product definition.

## 2. User and positioning

The clearest niche is a user whose preferred AI reasoning environment and repository execution environment do not line up cleanly. The reason may be product boundaries, workspace configuration, network topology, local-only state, provider choice, or simply a missing native integration.

The product story need not enumerate those causes:

> RepoReach lets your AI work with your Git repositories, wherever they are.

Users choose repositories and semantic capabilities. The first consumer product uses RepoReach Relay as the standard route; transport alternatives remain an internal/advanced concern unless later evidence justifies exposing them.

## 3. Scope

RepoReach is Git-specific, model/provider agnostic, repository-host agnostic, transport agnostic and locally policy-controlled.

Repositories may be local-only, GitHub, GitLab, Bitbucket or self-hosted. RepoReach works against the authorised local repository identity and ordinary Git remotes where local policy permits push.

Generic filesystem, desktop, database and machine control are out of scope.

## 4. RepoReach Relay

RRR is the standard hosted HTTPS/WSS rendezvous and delivery layer for the first consumer product. Its job is to make connection setup boring: install, pair, connected.

RRR does not run Git or become source of truth. Local RR owns discovery, repository identity, exact-base checks, worktrees, validation commands, commits, push policy and replay.

RRR supplies pairing, browser/WebMCP and API surfaces, short-lived queue/result state, endpoint wake-up over an outbound connection, and later managed-service convenience if users value it.

The Git core remains transport-neutral internally, but RepoReach will not spend MVP effort on simultaneous multipath delivery, automatic failover or route scoring. The existing Drive transport remains in the codebase as a proven development/compatibility path; whether it is exposed as a supported free consumer route is deliberately undecided.

## 5. Zero-cash deployment

Initial RRR prototype:
- Cloudflare Workers Free;
- SQLite-backed Durable Objects;
- hibernating WebSockets;
- Worker-hosted static assets;
- default provider hostname;
- no R2 dependency;
- no external identity provider;
- no billing.

The free platform's hard limits are a feature at this stage. RR imposes lower quotas and fails explicitly rather than create surprise spending.

## 6. Pairing before accounts

The MVP starts with endpoint/browser pairing rather than a global account system.

Local RR creates an opaque endpoint identity and high-entropy credential. A short-lived browser pairing challenge is confirmed locally and produces a revocable scoped browser session.

A conventional user/account layer arrives only when paid service or multi-device use actually requires it.

## 7. Open-source and paid boundary

The local Git capabilities do not change by subscription tier. Payment buys hosted relay capacity and convenience rather than withheld Git functionality.

RRR begins with a useful free semantic-operation allowance so users can experience the normal product path. Paid service can provide a much larger allowance plus validated conveniences such as multiple endpoints, easier recovery and support.

Meter user-comprehensible RepoReach operations, not HTTP requests, WebSocket frames or payload chunks. Choose the free allowance only after dogfood data shows ordinary monthly usage.

The existing Drive implementation stays in source as engineering/provenance. Publicly exposing or supporting it as a free alternative is a later product decision, not an MVP commitment.

## 8. Commercial objective

This is a small-business experiment, not a venture-scale thesis.

About $2,500/month recurring revenue would already be a satisfying maintenance-sustaining outcome.

Illustrative arithmetic:
- 125 users at $20/month = $2,500 MRR;
- about 167 at $15/month = about $2,500 MRR;
- 250 at $10/month = $2,500 MRR.

These are scenarios, not forecasts.

## 9. Payment timing

Current price hypothesis: roughly $15-$20/month for managed RRR.

Do not build billing before external willingness-to-pay evidence. Begin a paid pilot when either 10 external users are weekly active and 5 say they would pay in that range, or 3 external users are ready to become actual paid pilots.

Do not activate fixed recurring infrastructure until settled revenue covers it with substantial margin.

## 10. Competitive position

Raw AI-to-repository connectivity is increasingly commoditised. RepoReach should not claim uniqueness because an AI can edit Git.

The stronger position is:

> a controlled, durable Git transaction boundary that can use whichever connection path the AI environment makes available.

Defensibility, if it develops, comes from reliable compatibility across fragmented environments, installation/diagnostics, precise Git semantics and accumulated operational knowledge rather than a proprietary wire protocol.

## 11. Transport strategy

Externally, RepoReach is RRR-first. The normal installer and documentation should not ask users to choose Drive, MCP or a relay.

Internally, the core remains transport-neutral. The existing Drive/rclone implementation is retained as a development/compatibility asset; direct MCP and other routes can be added later if concrete environments demand them. Automatic multipath arbitration, failover and cross-transport deduplication are deferred because RRR is intended to cover the ordinary product path.

There is a hard boundary: an AI environment exposing no usable browser/tool/API surface capable of communicating with RRR cannot autonomously reach RR. RepoReach reports that condition rather than abusing public search or side-effecting URLs.

## 12. Trust and data

RRR durably needs endpoint/session/entitlement identifiers, not a source-code corpus. Requests/results/payloads are short lived and deleted early after acknowledgement with an absolute TTL.

Repositories, Git credentials, private local paths and durable Git audit history stay local by default. Do not claim universal end-to-end encryption unless the implemented route truly supplies it.

## 13. First public release

Target experience:
1. install RepoReach;
2. select repository roots;
3. choose read/edit/push capabilities;
4. pair RepoReach Relay once;
5. verify connectivity;
6. use the chosen AI normally.

Recursive discovery makes future repositories beneath approved roots appear without manual registration. Advanced transport details live in diagnostics.

A signed/notarised Mac application and Homebrew route remain appropriate first distribution targets. Cross-platform packaging follows evidence.

## 14. Naming migration

Use RepoReach/RR and RRR in new product documentation now.

Do not rename live repository/package/config/daemon identifiers during the separate P12-P15 programme. When it reaches a stable integration point, perform a tested migration that introduces reporeach as the primary CLI, migrates existing state safely, preserves bounded compatibility, and updates the client skill and protocol docs coherently.

## 15. Shared substrate and future ConvoReach sibling

RepoReach should preserve a narrow domain-neutral substrate for endpoint identity, pairing, scoped sessions, relay envelopes, reconnect/backoff, bounded payloads, quotas and connectivity diagnostics. Git discovery, policy, worktrees, validation, commits and push remain strictly RepoReach-specific.

This seam is intentionally designed so a future ConvoReach product can be a minimal sibling rather than a copy-paste fork. ConvoReach's defining test case is communication between already-existing native conversations — for example, asking from a ChatGPT thread what an existing Claude/Gemini/other ChatGPT thread has done or is doing — while preserving each conversation's own history, tools and provider context.

Do not create a generic framework or a third shared repository before a ConvoReach prototype exists. Keep the seam internal and testable; extract a tiny shared package only when two real products need it.

## 16. Next implementation programme

After this design freeze:
1. preserve current Git-core semantics;
2. confirm/extract the narrow shared Reach substrate without Git-specific dependencies;
3. build the smallest zero-cash RRR Worker/Durable Object service with a domain-neutral routing/account layer;
4. pair one local endpoint;
5. expose one browser/WebMCP surface;
6. materialise and edit a disposable repository through RRR;
7. prove RRR retry/reorder/duplicate delivery cannot duplicate mutation;
8. package the guided `install -> pair -> connected` setup;
9. measure semantic operation usage and set the free allowance from evidence;
10. recruit external alpha users;
11. defer billing, paid infrastructure and additional transports until evidence requires them.

Canonical detail:
- docs/product/reporeach-architecture.md
- docs/product/reporeach-validation.md
- docs/product/reach-shared-substrate.md
