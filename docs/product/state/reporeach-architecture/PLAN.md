# RepoReach Product Architecture Programme — PLAN

Status: COMPLETE — revised 2026-09-18
Date: 2026-09-18

## Objective
Define and freeze the product architecture for RepoReach (RR) and RepoReach Relay (RRR) on top of the current bridge implementation, while preserving a deliberately narrow shared connection substrate that a future ConvoReach sibling can reuse without importing Git semantics.

Completion requires repository-canonical specifications for:
- RR Git-domain boundaries;
- RRR-first consumer connectivity;
- relay authentication, pairing, data lifecycle and semantic-operation quotas;
- zero-initial-cash deployment;
- first-release user experience;
- naming/migration sequencing;
- commercial validation;
- the minimal reusable Reach substrate and its product isolation rules.

## Frozen requirements
- Product: RepoReach (RR).
- Hosted relay: RepoReach Relay (RRR).
- Scope: Git repositories wherever they live; not generic computer control.
- Provider/model agnostic.
- **RRR-first externally, transport-neutral internally.** Normal consumer onboarding is install -> pair -> connected, not a transport chooser.
- Do not invest MVP effort in multipath arbitration, automatic failover, route scoring or simultaneous cross-transport delivery.
- The existing Drive/rclone mechanism remains proven code and an engineering/compatibility asset. Whether it is publicly exposed as a supported free route is deliberately undecided.
- Local repositories and local RR policy remain authoritative.
- RRR must not become a Git host.
- Initial cash investment remains $0.
- Local Git capabilities are not tier-gated. Hosted RRR begins with a useful free allowance and may charge for substantially higher semantic-operation usage and service convenience.
- Meter product-level semantic operations, not HTTP requests, WebSocket frames or payload chunks.
- Approximately $2,500/month recurring revenue is already a successful commercial outcome.
- Product-facing language describes capabilities rather than centring institutional restrictions.
- A future ConvoReach must be able to reuse connection infrastructure without RepoReach acquiring conversation/room semantics.

## Shared Reach substrate
The reusable seam is intentionally small: endpoint identity, pairing, product-scoped sessions, authenticated relay envelopes, reconnect/backoff, bounded payload transfer, correlation/acknowledgement, expiry, quota hooks and sanitized connectivity diagnostics.

RepoReach alone owns repository identity, Git request/result semantics, snapshots/materialisation, exact-base checks, worktrees, validation, commit/push authority, Git replay/recovery and self-update semantics.

A future ConvoReach sibling would own native-conversation identity, provider/session adapters, room/participant membership, addressed turns, reply/correlation provenance and bounded conversation-turn policy. Product credentials/authority must remain isolated.

Do not create a speculative third `reach-core` repository now. Keep the seam internal until two real products demonstrate enough shared code to justify extraction.

## Canonical locations
- Repository: llm-git-bridge / public product identity RepoReach.
- Integration branch: ai/reporeach-product-architecture-20260918.
- Programme state: docs/product/state/reporeach-architecture/.
- Canonical design documents:
  - docs/product/reporeach-architecture.md
  - docs/product/reporeach-validation.md
  - docs/product/reach-shared-substrate.md
  - docs/product-and-managed-service-strategy.md

## Related idea-management programme
A separate Work Ecosystem Management & Interfaces programme in `tom-work-admin` has already designed a first-class ideas registry with stable IDs, lifecycle, provenance, fork/convergence relations and temporary `ideas/inbox` storage. The live materialised tree inspected on 2026-09-18 did not yet contain `registry/ideas.yaml` or `ideas/inbox`; implementation remains with that programme. ConvoReach therefore remains an unpromoted idea rather than being turned into a project/repository here.

The defining ConvoReach use case to capture when that registry exists is: from one existing AI conversation, address another already-existing ChatGPT/Claude/Gemini conversation, ask what it has done/is doing, and bring the response back with provenance without recreating either conversation as an API model instance.

## In scope
Product architecture, RRR-first relay design, auth/pairing/revocation, data minimisation/retention, security boundaries, zero-cash hosting, onboarding, semantic quotas, commercial boundary, validation metrics, migration sequencing and the shared Reach seam.

## Out of scope
Production RRR deployment, billing implementation, domain purchase, repository/package/CLI rename, P12-P15 Git-parity implementation, public Drive support decision, ConvoReach implementation, a generic agent bus, general machine control, formal trademark clearance.

## Completion checks
The design must not:
- require users to configure or understand multiple transports in ordinary use;
- make multipath a release gate;
- confuse web search/retrieval with authenticated bidirectional tool access;
- make server-side RRR policy capable of overriding stricter local RR policy;
- expose local filesystem paths or Git credentials;
- retain source snapshots indefinitely;
- create unbounded storage/compute cost;
- meter low-level relay implementation details as user-visible usage;
- promise end-to-end encryption where not technically true;
- require paid infrastructure before revenue;
- broaden RepoReach beyond Git;
- make ConvoReach import Git-domain modules;
- prematurely extract a generic framework solely for hypothetical reuse;
- rename the live package/repository while P12-P15 are active.

## Recovery and timeout discipline
Use exact fresh bases, safe branches, small documentation transactions, durable-result inspection and independent materialisation. After an ambiguous mutation, inspect actual branch/result state before retry. Do not mutate protected main for this architecture programme.
