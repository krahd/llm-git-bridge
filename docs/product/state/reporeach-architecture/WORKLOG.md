# RepoReach Product Architecture Programme — WORKLOG

## 2026-09-18 — recovery and scope freeze
Recovered current repository reality and the prior commercial-strategy checkpoint instead of restarting from chat history.

Frozen decisions:
- RepoReach (RR) is the product.
- RepoReach Relay (RRR) is the hosted relay.
- RR remains Git-repository-specific.
- Transport details should normally disappear from the user's successful workflow.
- Zero initial cash investment is a hard constraint.
- Roughly $2,500/month recurring revenue is already a successful commercial outcome.
- Public product language should say what RR offers rather than centre institutional restrictions.

Recovered repository facts:
- current main baseline: 93608ec63566a7296abc6888134a9cf435e52481;
- product branch: ai/product-strategy-checkpoint-20260917;
- existing product-strategy commit: 18b7fd6f2b43fa54f93af14ce6afa0408e0d4d04.

Research refresh:
- OpenAI site tools/WebMCP and browser use are separate from installed plugins/apps.
- Workers Free currently supports 100,000 requests/day.
- SQLite Durable Objects are free-tier capable, support hibernating WebSockets and can hold early relay state; rows/values are capped at 2 MB.
- R2 is not required for the zero-cash MVP because activating it involves a subscription/checkout path.
- Static assets can be hosted without separate static-asset request charges.

Architecture findings already established:
1. RRR is a thin relay/control plane, never the Git execution environment or source of truth.
2. RR is multipath: RRR, MCP and mailbox transports may operate concurrently against one core protocol.
3. Duplicate delivery is expected and deduplicated by transaction ID plus canonical request hash, not transport ordering.
4. Local RR is final authority for repository policy and mutation.
5. Hosted RRR stays optional to complete self-managed RR.
6. Search-only web retrieval is not an authenticated bidirectional transport.
