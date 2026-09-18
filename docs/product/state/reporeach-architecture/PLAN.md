# RepoReach Product Architecture Programme — PLAN

Status: active
Date: 2026-09-18

## Objective
Freeze a coherent product architecture for RepoReach (RR) and RepoReach Relay (RRR) that grows from the existing llm-git-bridge implementation without discarding its proven Git safety, durability, discovery, concurrency, current-branch and self-update work.

Completion requires canonical repository documents for architecture, security/data lifecycle, zero-cash relay deployment, transport abstraction, first-release UX, commercial boundary and validation roadmap.

## Frozen user requirements
- Product name: RepoReach; shorthand/mark: RR.
- Hosted relay: RepoReach Relay; shorthand: RRR.
- Scope is Git repositories wherever they live, not arbitrary computer control.
- Provider/model and transport remain replaceable.
- Ordinary users should not need to understand whether Drive, MCP, RRR or another transport carried a request.
- Product-facing language describes capabilities rather than centring institutional restrictions.
- Initial cash investment remains $0.
- Small sustainable economics are sufficient; about $2,500/month recurring revenue would already be a successful outcome.
- Open-source/self-managed RR remains complete; paid value should be managed convenience, reliability and support.
- GitHub/repository state remains canonical.

## Canonical locations
- Repository: llm-git-bridge / llm-git-bridge-a91071ea.
- Product branch: ai/product-strategy-checkpoint-20260917.
- Existing checkpoint: docs/product-and-managed-service-strategy.md.
- Programme state: docs/product/state/reporeach-architecture/.
- Final documents:
  - docs/product/reporeach-architecture.md
  - docs/product/reporeach-validation.md
  - revised docs/product-and-managed-service-strategy.md

The independent Root Policy / full-Git-parity P12–P15 programme must not be overwritten.

## In scope
Naming migration; RR core/adapter/RRR boundaries; multipath transport; RRR protocol/state machine; browser/WebMCP/API/MCP/mailbox surfaces; pairing/auth/revocation; data minimisation and retention; zero-cash infrastructure; free/open-source versus hosted/paid responsibilities; first-release UX; validation metrics and commercial triggers; implementation sequence.

## Out of scope
Production RRR deployment, payments, domain purchase, immediate repo/package/CLI rename, rewriting the Git core, P12–P15 implementation, formal trademark clearance, or claiming operation when an AI environment exposes no usable bidirectional capability.

## Verified baseline
- Fresh main materialisation: 93608ec63566a7296abc6888134a9cf435e52481.
- Existing product strategy commit/push: 18b7fd6f2b43fa54f93af14ce6afa0408e0d4d04 on ai/product-strategy-checkpoint-20260917.
- Current RR predecessor capabilities include read/edit/push/current-branch-write/self-update.

## Current external evidence
Checked 2026-09-18:
- ChatGPT desktop site tools use WebMCP and can work without a separately installed connector when browser/account support exists.
- Browser use is a separate capability surface from installed apps/plugins.
- Cloudflare Workers Free currently includes 100,000 requests/day.
- SQLite-backed Durable Objects are available on Workers Free and support hibernating WebSockets.
- Durable Object values/rows are capped at 2 MB, so larger payloads require chunking.
- R2 offers free usage but requires an R2 subscription/checkout; zero-cash MVP must not depend on it.
- Worker static assets do not add separate static-asset request charges.

These are dated implementation inputs, not permanent vendor promises.

## Phases
P0 recover exact product branch and existing checkpoint.
P1 refresh only time-sensitive architecture evidence until searches saturate.
P2 draft RR/RRR architecture, transport contract, auth, data lifecycle, UX and commercial boundary.
P3 adversarially audit hidden SaaS/plugin dependencies, replay, cost, privacy, auth, scope creep and migration risk; repair all material findings.
P4 persist programme state on the existing product branch.
P5 create/revise the canonical product documents and verify exact readback.
P6 final adversarial completion audit; mark state COMPLETE only after branch materialisation proves final documents.

## Architecture audit requirements
The final design must not:
- make RRR mandatory for self-managed RR;
- make MCP/plugins mandatory;
- confuse search-only web retrieval with authenticated bidirectional access;
- turn RRR into a Git host or source of truth;
- allow server authority to bypass local RR policy;
- depend on transport ordering;
- create unbounded storage/cost;
- expose local paths, Git credentials or history unnecessarily;
- promise end-to-end encryption where the invocation surface cannot provide it;
- require a custom domain, paid Worker plan, R2, or external auth vendor for the MVP;
- broaden the product beyond Git;
- destabilise concurrent P12–P15 development by renaming code immediately.

## Recovery discipline
Use small documentation-only transactions on the product branch. After stale/ambiguous operations, inspect authoritative branch state before retry. Never mutate protected main merely to finish this architecture programme.
