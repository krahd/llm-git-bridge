# RepoReach Product Architecture Programme — PLAN

Status: active
Date: 2026-09-18

## Objective
Define and freeze the product architecture for RepoReach (RR) and RepoReach Relay (RRR) on top of the current bridge implementation, without disrupting the independent Git-parity roadmap.

Completion requires repository-canonical specifications for:
- RR core and transport boundaries;
- RRR protocol, authentication, pairing and data lifecycle;
- transparent multipath transport selection;
- zero-initial-cash deployment;
- open-source versus hosted responsibilities;
- first-release user experience;
- naming/migration sequencing;
- commercial validation and objective triggers for paid infrastructure.

## Frozen requirements
- Product: RepoReach (RR).
- Hosted relay: RepoReach Relay (RRR).
- Scope: Git repositories wherever they live; not generic computer control.
- Provider/model agnostic and transport agnostic.
- A normal successful workflow must not require the user to understand Drive, MCP, WebMCP, or relay mechanics.
- Local repositories and local RR policy remain authoritative.
- RRR must not become a Git host or mandatory SaaS dependency.
- Initial cash investment remains $0.
- The self-managed/open-source product remains complete.
- Paid value, if validated, is managed convenience, reliability and support.
- Approximately $2,500/month recurring revenue is already a successful commercial outcome.
- Product-facing language describes capabilities rather than centring institutional restrictions.

## Canonical locations
- Repository: llm-git-bridge / public product identity RepoReach.
- Integration branch: ai/reporeach-product-architecture-20260918.
- Integration base at programme migration: main 93608ec63566a7296abc6888134a9cf435e52481.
- Programme state: docs/product/state/reporeach-architecture/.
- Final documents:
  - docs/product/reporeach-architecture.md
  - docs/product/reporeach-validation.md
  - docs/product-and-managed-service-strategy.md

Historical product branch ai/product-strategy-checkpoint-20260917 and its state are evidence only after this migration; they are not the canonical continuation base.

## In scope
Product architecture, transport contract, RRR relay design, auth/pairing/revocation, data minimisation/retention, security boundaries, zero-cash hosting, onboarding, commercial boundary, validation metrics and migration sequencing.

## Out of scope
Production RRR deployment, billing implementation, domain purchase, repository/package/CLI rename, P12-P15 Git-parity implementation, general machine control, formal trademark clearance.

## External implementation inputs checked 2026-09-18
- ChatGPT desktop site tools can expose WebMCP tools without a separately installed connector where the browser/account supports them.
- Browser capability is separately governable from installed apps/plugins.
- Cloudflare Workers Free currently includes 100,000 Worker requests/day.
- SQLite Durable Objects are available on Workers Free and support hibernating WebSockets.
- Durable Object values/rows have a 2 MB ceiling, requiring bounded chunking for larger payloads.
- R2 has a useful free allowance but activation uses a subscription/checkout flow; it is therefore excluded as an MVP dependency under the strict zero-initial-cash constraint.
- Static assets can be served without a separate paid hosting product.

These are dated vendor facts, not permanent product guarantees.

## Phases
P0 recover repository and prior strategy state.
P1 refresh time-sensitive platform/hosting evidence to saturation.
P2 design RR/RRR architecture and first-release UX.
P3 adversarially audit architecture against access, auth, replay, privacy, cost, vendor lock-in, scope creep and migration failure.
P4 persist canonical execution state on a fresh branch from current main.
P5 write canonical architecture/validation/strategy documents in bounded documentation transactions.
P6 independently materialise and inspect final branch, repair any material defect, then mark state COMPLETE.

## Non-negotiable architecture checks
The design must not:
- require RRR for self-managed RR;
- require MCP, a plugin, or Drive specifically;
- confuse web search/retrieval with authenticated bidirectional tool access;
- make server-side RRR policy capable of overriding stricter local RR policy;
- depend on FIFO transport delivery;
- expose local filesystem paths or Git credentials;
- retain source snapshots indefinitely;
- create unbounded storage/compute cost;
- promise end-to-end encryption on surfaces where the server necessarily sees plaintext;
- require a paid plan, custom domain, R2, or external identity provider for MVP;
- broaden RR beyond Git;
- rename the live package/repository while P12-P15 are active.

## Recovery and timeout discipline
Use exact fresh bases, safe branches, small documentation-only transactions, durable-result inspection, and independent materialisation. After an ambiguous mutation, inspect actual branch/result state before retry. Do not mutate protected main for this architecture programme.
