# RepoReach Product Architecture Programme — CONTINUATION PROMPT

Continue the RepoReach (RR) / RepoReach Relay (RRR) product-architecture programme without restarting completed bridge implementation or market research.

Read in this order:
1. STATUS.md
2. PLAN.md
3. latest WORKLOG.md
4. this file
5. docs/product-and-managed-service-strategy.md
6. actual current materialised product branch

Repository evidence outranks stale state text.

Frozen requirements:
- RepoReach / RR; RepoReach Relay / RRR.
- Git repositories wherever they live; not generic machine control.
- Provider/model and transport agnostic.
- Ordinary successful use should not require choosing Drive, MCP or relay.
- Multipath adapters may operate concurrently and converge on one transaction core.
- Local repositories remain authoritative; RRR is never a Git host or policy authority.
- Zero initial cash investment.
- Complete open-source/self-managed RR; paid value is managed convenience/reliability/support.
- About $2,500/month recurring revenue is already a successful commercial target.
- Product-facing wording describes capabilities rather than centring institutional restrictions.

Current architecture direction:
- Zero-cash RRR prototype: Cloudflare Workers Free + SQLite Durable Objects + static assets on a provider domain.
- Local daemon connects outbound over HTTPS/WSS; hibernating WebSockets provide wake-up without a permanent server process.
- Durable Object storage keeps bounded short-lived relay state/payload chunks; R2 is not required for MVP.
- Pairing uses local device identity and short-lived confirmation, without requiring an external auth vendor.
- LLM-facing surfaces can include REST/API, MCP adapters, ChatGPT site tools/WebMCP and interactive browser UI.
- Search-only browsing is not sufficient for authenticated bidirectional Git work.

Exact next action after state persistence:
Draft and adversarially audit docs/product/reporeach-architecture.md, docs/product/reporeach-validation.md, and the superseding revision of docs/product-and-managed-service-strategy.md. Continue automatically; do not ask for approval.

Completion gate:
Exact branch materialisation/readback proves the final documents and state exist; the final audit confirms zero-cash feasibility, optional-SaaS property, local policy authority, transport transparency, Git-specific scope, coherent naming migration and objective commercial trigger criteria. Then STATUS becomes COMPLETE and this prompt records implementation/deployment as the next separate programme.
