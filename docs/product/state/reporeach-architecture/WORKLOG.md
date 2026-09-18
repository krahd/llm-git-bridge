# RepoReach Product Architecture Programme — WORKLOG

## 2026-09-18 — recovery, naming and architecture freeze

User decisions:
- RepoReach (RR) is the product.
- RepoReach Relay (RRR) is the hosted relay.
- RR remains Git-specific.
- Users should not need to manage or understand transport selection in ordinary successful use.
- Zero initial cash investment remains mandatory.
- Roughly $2,500/month recurring revenue would already justify the project commercially.

Recovered engineering baseline:
- current main/runtime: 93608ec63566a7296abc6888134a9cf435e52481;
- direct-current-branch and structured self-update already proven;
- current bridge provides repository discovery, path-free public identity, exact-base transactions, constrained validation commands, controlled commit/push and replay/durability semantics.

Research refresh:
- browser/site-tool capability can remain available independently of installed plugins/apps;
- WebMCP site tools provide a promising browser-facing RR surface where supported;
- search-only retrieval is not a transactional transport;
- Workers Free + SQLite Durable Objects + static assets can support a $0-cash relay prototype;
- hibernating WebSockets avoid a permanently running server process;
- Durable Object 2 MB row/value limits require chunking;
- R2 is deferred because it is not necessary for MVP and its activation path introduces a subscription/checkout boundary.

Architecture decisions:
1. RRR is a thin relay/control plane, not a Git execution environment or repository host.
2. RR is multipath: RRR, MCP and mailbox transports may coexist.
3. Every transport feeds one canonical transaction core; duplicate delivery is normal and deduplicated by transaction ID plus canonical request hash.
4. Transport ordering is never a dependency.
5. Local RR is final authority for repo capabilities and mutation.
6. Hosted RRR remains optional; self-managed RR must stay complete.
7. Pairing is device-centric and can begin without an external identity provider.
8. Payload retention is short and bounded; no Git credentials/local paths belong in RRR.
9. Stronger client-side encryption may be added where technically real, but architecture must not make false universal E2EE claims.
10. Naming/code migration waits until concurrent Git-parity work is stable.

## 2026-09-18 — branch migration
A first persistent state checkpoint was written to the pre-existing ai/product-strategy-checkpoint-20260917 branch. Adversarial review then found that branch to be older than current RC9 main. Continuing there would create avoidable integration debt and a stale product specification. The programme therefore migrates canonically to ai/reporeach-product-architecture-20260918 from exact current main. The older branch is historical evidence only.
## 2026-09-18 — canonical documents and final adversarial audit
- Migrated the programme from the stale historical product branch to ai/reporeach-product-architecture-20260918 based on exact current main.
- Canonical strategy, architecture and validation documents committed and pushed at afe93194932bdb2ad63500b5621b7ebf8d2bef81; independent materialisation matched that exact head.
- Re-checked current OpenAI browser/site-tool facts and Cloudflare Workers/Durable Object limits. R2 remains deliberately outside the strict zero-cash MVP.
- Adversarially audited hidden plugin/MCP dependency, browser availability, multipath replay, server cost, data retention, auth/pairing, scope creep, naming migration and commercial falsifiers.
- Found and repaired the relay-trust wording gap: RRR compromise cannot exceed local RR policy but can attempt operations inside granted remote authority; the product must not call this zero-trust.
- No remaining material design defect blocks architecture freeze. Implementation/prototype work is now a separate programme.

## 2026-09-18 — RRR-first revision and ConvoReach seam

New user decisions reopened the completed architecture programme:
- do not spend MVP effort on multipath if RRR can be the standard connection path;
- keep the existing Drive/rclone transport in the codebase, but decide later whether exposing/supporting it publicly would undermine the hosted service;
- provide a useful free RRR allowance and charge for heavier semantic-operation usage rather than gating Git capabilities;
- design RepoReach so a future ConvoReach can be a minimal sibling.

Reconciled a separate live Work Ecosystem Management & Interfaces programme for `tom-work-admin`. It already specifies the missing ideas entity: stable idea IDs, lifecycle `inbox -> exploring -> developing -> promoted | parked | retired`, provenance, fork/convergence relations, `registry/ideas.yaml`, and temporary unowned content under `ideas/inbox`. Its live tree did not yet contain those files, so this programme did not duplicate that implementation or promote ConvoReach prematurely.

The defining ConvoReach use case was clarified by the user: “use ConvoReach and see what the other convo has done/is doing” describes desired future behaviour, not a request to use an existing tool. The architecture now treats an already-existing native conversation as an addressable participant whose own history/project/tools remain authoritative.

Architecture repair:
1. Changed RepoReach from multipath-first to RRR-first externally / transport-neutral internally.
2. Removed multipath arbitration, automatic failover and cross-transport duplicate delivery from MVP gates.
3. Retained Drive/rclone as proven internal/compatibility code while leaving public exposure/support undecided.
4. Changed the commercial boundary to full local Git capability plus a useful free RRR semantic-operation allowance, with paid higher use/service convenience.
5. Added `docs/product/reach-shared-substrate.md` defining reusable endpoint identity, pairing, sessions, relay envelope, reconnect, bounded payloads, quota hooks and diagnostics.
6. Kept all Git semantics exclusively inside RepoReach and defined future ConvoReach-only conversation/session/provenance semantics.
7. Explicitly rejected a premature third `reach-core` repository; extraction occurs only after two real products prove the seam.

One broad hand-constructed patch failed cleanly at `git apply`; authoritative branch state showed no mutation. It was replaced by smaller exact-base documentation patches, each inspected through durable bridge results.
