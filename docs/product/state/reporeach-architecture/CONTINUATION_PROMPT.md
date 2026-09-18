# RepoReach Product Architecture Programme — CONTINUATION PROMPT

Resume the RepoReach / RepoReach Relay product-architecture programme from actual repository reality.

Read in order:
1. STATUS.md
2. PLAN.md
3. latest WORKLOG.md
4. this file
5. docs/product-and-managed-service-strategy.md if present
6. actual current materialised branch

Canonical integration branch:
ai/reporeach-product-architecture-20260918, created from main 93608ec63566a7296abc6888134a9cf435e52481.

Do not resume the older ai/product-strategy-checkpoint-20260917 branch as canonical. It is historical evidence.

Frozen requirements:
- RepoReach / RR; RepoReach Relay / RRR.
- Git repositories wherever they live; not generic machine control.
- Provider/model and transport agnostic.
- Successful ordinary use hides transport choice.
- Multipath adapters may coexist and converge on one transaction core.
- Local Git repositories and local RR policy remain authoritative.
- RRR is never a Git host or mandatory dependency.
- Zero initial cash investment.
- Complete open-source/self-managed RR.
- Paid value is managed convenience/reliability/support only after validation.
- About $2,500/month recurring revenue is already a successful commercial target.

Current architecture direction:
- RRR MVP: Workers Free + SQLite Durable Objects + static assets on a provider domain.
- Local RR makes outbound HTTPS/WSS connections; hibernating WebSockets provide wake-up.
- Bounded short-lived relay state and chunked payloads live in Durable Objects; R2 is not required for MVP.
- Pairing uses a local device identity plus short-lived single-use confirmation; no external auth vendor is required initially.
- LLM-facing surfaces may include REST/API, MCP adapters, WebMCP/site tools and an interactive browser interface.
- Search-only browsing is not sufficient for authenticated bidirectional repository work.

Exact next action after state verification:
Draft docs/product/reporeach-architecture.md, docs/product/reporeach-validation.md and docs/product-and-managed-service-strategy.md; adversarially audit them against PLAN; commit/push in bounded documentation transactions; independently materialise/read back; repair if needed.

Completion gate:
Final exact branch materialisation proves all canonical documents and state; adversarial audit finds no material architecture defect; STATUS says COMPLETE; continuation records implementation/prototype work as a separate next programme.
