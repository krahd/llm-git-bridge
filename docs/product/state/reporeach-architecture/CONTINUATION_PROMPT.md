# RepoReach Product Architecture Programme — CONTINUATION PROMPT

This architecture programme is COMPLETE. Do not restart its research or branch-migration work merely because older checkpoints contain in-progress instructions.

Canonical branch:
ai/reporeach-product-architecture-20260918

Canonical documents:
- docs/product-and-managed-service-strategy.md
- docs/product/reporeach-architecture.md
- docs/product/reporeach-validation.md
- docs/product/state/reporeach-architecture/PLAN.md
- STATUS.md / WORKLOG.md / this file in the same state directory

Frozen product decisions:
- RepoReach (RR) is Git-specific, provider/model agnostic and transport agnostic.
- RepoReach Relay (RRR) is optional hosted convenience, never a Git host or source of truth.
- Multipath transports converge on one canonical replay-safe transaction core.
- Local Git repositories and local RR policy remain authoritative.
- Ordinary successful use hides transport choice.
- Complete open-source/self-managed RR remains available.
- Zero initial cash investment remains the pre-revenue infrastructure rule.
- About $2,500/month recurring revenue is already a successful commercial target.
- RRR is within the remote-request trust boundary: compromise can attempt operations inside granted local authority, though it cannot bypass stricter local RR policy.

Do not immediately rename repository/package/config/daemon identifiers while the independent P12-P15 Git-parity programme is active.

Next separate implementation programme:
1. resume P12-P15 from their own canonical state until a safe integration checkpoint;
2. extract/confirm a transport-neutral daemon adapter;
3. add cross-transport canonical deduplication tests;
4. implement a local mock relay;
5. implement Workers Free + SQLite Durable Object RRR with hibernating WebSocket, no R2/billing/domain dependency;
6. implement pairing/revocation/TTL/quota controls;
7. add browser/WebMCP and MCP adapters;
8. prove disposable-repository read/edit and simultaneous duplicate-delivery canaries;
9. then package/naming migration and external alpha validation.

Before implementation, re-check dated vendor limits/capabilities. Preserve timeout-safe micro-steps and inspect reality before retrying ambiguous mutations.
