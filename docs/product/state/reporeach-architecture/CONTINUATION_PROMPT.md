# RepoReach Product Architecture Programme — CONTINUATION PROMPT

This architecture programme is COMPLETE after the 2026-09-18 RRR-first/ConvoReach revision. Do not restart the older multipath design because historical checkpoints contain it.

Canonical branch:
`ai/reporeach-product-architecture-20260918`

Read actual repository state before relying on any recorded SHA. Canonical documents:
- `docs/product-and-managed-service-strategy.md`
- `docs/product/reporeach-architecture.md`
- `docs/product/reporeach-validation.md`
- `docs/product/reach-shared-substrate.md`
- state files in `docs/product/state/reporeach-architecture/`

Frozen product decisions:
- RepoReach (RR) remains Git-specific.
- RepoReach Relay (RRR) is the standard consumer connection route. Normal UX is install -> pair -> connected.
- Keep the Git engine transport-neutral internally, but do not build multipath arbitration/failover/route scoring for MVP.
- The existing Drive/rclone mechanism remains proven code; public exposure/support is intentionally undecided.
- Local repositories and local RR policy remain authoritative; RRR is not a Git host.
- Free and paid users have the same local Git capabilities. Hosted RRR has a useful free semantic-operation allowance; higher relay use/service convenience may be paid. Do not expose raw HTTP/WebSocket/chunk counts as the user-facing meter.
- Zero initial cash remains the pre-revenue infrastructure constraint.
- About $2,500/month recurring revenue is already a successful commercial target.

Shared Reach seam:
- reusable: endpoint identity, pairing, product-scoped sessions, authenticated relay envelope, reconnect/backoff, correlation/acknowledgement, expiry, bounded payload transfer, semantic quota hooks and sanitized diagnostics;
- RepoReach-only: all repository/Git semantics;
- future ConvoReach-only: native conversation identity/adapters, participants/rooms, addressed turns, reply/correlation provenance and bounded turn policy.
- product credentials/authorities must stay isolated; do not create a generic remote-execution API.
- do not create a separate `reach-core` repository until a real ConvoReach prototype proves extraction worthwhile.

ConvoReach is currently an idea, not a promoted project. The separate `tom-work-admin` ecosystem-management programme already designs the first-class ideas registry but had not yet implemented `registry/ideas.yaml`/`ideas/inbox` at the last inspection. Do not create a competing registry. Once available, ConvoReach should be captured there with the defining use case: one existing AI conversation can address another existing conversation and retrieve its current work/state with provenance without recreating either as an API model instance.

Next separate implementation programme:
1. resume P12-P15 from their own canonical state to a safe integration checkpoint;
2. extract/confirm the internal domain-neutral Reach seam;
3. build the smallest zero-cash RRR service as the normal consumer route;
4. implement pairing/revocation/TTL/quota controls and semantic-operation metering;
5. prove RRR retry/idempotency and Git read/edit flows;
6. ship install -> pair -> connected onboarding;
7. measure real free-tier usage before choosing X operations/month;
8. add/expose alternate transports only in response to concrete evidence;
9. perform RepoReach naming/package/repository migration at a safe engineering checkpoint.

Preserve timeout-safe micro-steps and inspect reality before retrying ambiguous mutations.
