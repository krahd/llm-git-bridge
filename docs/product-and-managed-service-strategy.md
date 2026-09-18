# Product, transport, and managed-service strategy

Status: active design checkpoint
Date: 2026-09-17

This document is intentionally self-contained so the product discussion can resume without relying on chat history.

## 1. Original problem and product motivation

The concrete problem that motivated `llm-git-bridge` is not generic Git automation. Tomas uses ChatGPT through the CU workspace, and CU has not enabled the Git/GitHub plugin/connector for him for reasons that are currently unknown. He nevertheless needs ChatGPT conversations to work against the authoritative Git repositories on his own Mac, including local/unpushed state, without repeatedly copying files or diffs by hand.

The bridge was therefore built as a provider-neutral local Git safety/execution layer with a transport that ChatGPT can already reach. The current transport is a Google Drive mailbox because the CU ChatGPT environment has usable Google Drive access.

The bridge core is deliberately more than transport. It provides repository discovery, path-free remote identity, filtered snapshots, exact-base/stale-SHA checking, safe-prefix branches, isolated Git worktrees, locally configured symbolic validation commands, controlled commits, two-key opt-in push policy, replay/durability controls, and no arbitrary remote shell interface.

## 2. Secure MCP Tunnel: what it changes

### Current evidence

OpenAI now provides Secure MCP Tunnel (`openai/tunnel-client`). It is a customer-run agent that connects a private or localhost MCP server to ChatGPT, Codex, the Responses API, and AgentKit through an OpenAI-hosted tunnel endpoint. The tunnel client initiates outbound HTTPS; the local MCP server does not need a public listener or inbound firewall rule.

Primary sources checked 2026-09-17:

- OpenAI Help, Developer mode and MCP apps in ChatGPT: https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt
- OpenAI Secure MCP Tunnel client: https://github.com/openai/tunnel-client
- Tunnel architecture: https://github.com/openai/tunnel-client/blob/master/docs/architecture.md

### Critical conclusion

Secure MCP Tunnel is **not a replacement for the Git safety/execution logic of `llm-git-bridge`**. It is potentially a replacement for, or additional option beside, the Google Drive/rclone **transport layer**.

Secure MCP Tunnel forwards MCP calls to a local MCP server. Something still has to implement the Git-facing tools and the safety semantics. `llm-git-bridge` can supply that layer.

A likely cleaner architecture for OpenAI users is therefore:

```text
ChatGPT
   |
custom MCP app
   |
OpenAI Secure MCP Tunnel
   |
local MCP adapter
   |
llm-git-bridge core
   |
local Git repositories
```

rather than throwing away the bridge and replacing it with a tunnel.

## 3. The CU-admin constraint is decisive

Secure MCP Tunnel does **not** automatically solve the original CU problem.

For ChatGPT Enterprise/Edu, OpenAI's current documentation says workspace administrators must grant Developer Mode / custom MCP connector access. Enterprise/Edu can use RBAC to grant it to selected users. Only admins/owners can publish custom apps. Full MCP including write/modify actions is currently a beta capability for Business, Enterprise and Edu.

Therefore the key distinction is:

- CU disabling/not enabling the GitHub app does not prove custom MCP is disabled.
- But Secure MCP Tunnel still requires CU to permit Tomas to create/use a custom MCP app (Developer Mode / Connected Data permissions).
- If CU has not granted that permission, Secure MCP Tunnel cannot simply bypass the workspace's connector governance.
- The current Drive mailbox works specifically because Google Drive is an already-available ChatGPT data path in the CU workspace.

So the first practical question is now: **does Tomas's CU ChatGPT role have Developer Mode / Create custom MCP connectors permission?**

If yes, the Secure MCP route should be prototyped before doing further Google-specific transport work. If no, the Drive transport remains a justified workaround rather than needless duplication.

The OpenAI tunnel also has its own Platform-side requirements: tunnel ID, runtime API key, and Tunnels Read/Use permissions for the principal running the daemon. Those are separate from ChatGPT workspace permission to create/use the custom MCP app.

## 4. Architectural direction

Preserve the core as provider-neutral and treat transports/adapters as replaceable.

```text
llm-git-bridge core
    |
    +-- Google Drive/rclone mailbox
    |     - works where Drive is already available in ChatGPT
    |     - useful fallback for locked-down workspaces
    |
    +-- MCP server adapter
    |     +-- OpenAI Secure MCP Tunnel
    |     +-- other MCP-capable clients/transports
    |
    +-- future managed relay, only if demand remains after native tunnel options
```

The MCP adapter should expose a deliberately constrained tool surface that maps onto existing bridge semantics rather than exposing arbitrary Git or shell operations. The existing invariants remain valuable: exact bases, safe branches, local validation policy, controlled push, replay handling, and local authoritative repositories.

The Secure MCP path may remove Drive polling, Google OAuth setup, and Drive mailbox latency for users whose ChatGPT workspace permits custom MCP. It should not weaken the bridge's local safety boundary merely because MCP can call tools directly.

## 5. Adversarial comparison: Drive vs Secure MCP Tunnel

### Google Drive transport

Advantages:

- already works in the CU environment where GitHub access is unavailable;
- does not require a ChatGPT custom-MCP permission if Drive access is already enabled;
- provider-neutral mailbox concept;
- durable asynchronous requests/results are naturally represented as files.

Disadvantages:

- Google OAuth/rclone setup is cumbersome for public users;
- rclone's shared Drive OAuth client is being retired, motivating per-user OAuth unless the design changes;
- polling and Drive round trips add latency and complexity;
- full Drive scope is currently used because mailbox files can be created by different OAuth clients.

### Secure MCP Tunnel transport

Advantages:

- purpose-built by OpenAI for connecting ChatGPT/Codex to private/local MCP servers;
- outbound-only; no public local server needed;
- removes Google-specific OAuth and Drive mailbox operations for eligible users;
- direct request/response path is conceptually cleaner than file polling;
- official operator tooling, health/readiness/metrics and Homebrew installation exist.

Disadvantages / constraints:

- ChatGPT Enterprise/Edu custom MCP use is admin/RBAC controlled;
- only useful in ChatGPT if the CU workspace permits Tomas to create/use the custom app;
- OpenAI-specific transport, so it cannot be the only transport if provider neutrality is a product goal;
- requires OpenAI Platform tunnel/runtime credentials and permissions;
- changes durability/replay assumptions: the bridge must not casually discard its proven transaction/replay safety simply because MCP is synchronous;
- tool payloads traverse OpenAI's tunnel infrastructure, which is a different trust/data-flow model from the Drive mailbox and must be documented accurately.

### Working conclusion

Do not delete or rewrite the bridge around Secure MCP Tunnel. **Add MCP as another client/transport boundary and test it.** If it works in CU, make it the preferred OpenAI path; retain Drive as a fallback and as evidence that the core is genuinely transport-neutral.

## 6. Product and commercial model

The intended public product is standalone, open-source software with a polished Mac installation path:

- signed/notarised `.app` / DMG, drag to Applications;
- Homebrew Cask / tap for technical users;
- local Git repositories remain authoritative;
- free self-managed operation remains complete, not crippleware.

Two product tiers are conceptually attractive:

### Free / self-managed

User runs the open-source bridge and supplies a transport available in their environment, for example:

- OpenAI Secure MCP Tunnel when their ChatGPT workspace permits custom MCP;
- Google Drive/rclone when that is the available integration path;
- future compatible transports.

### Paid / managed convenience

A small subscription should buy convenience/infrastructure, **not access to a secret shared Google OAuth client ID**.

The earlier candidate was an LLM Git Bridge managed relay. This remains an option only if native/provider tunnels do not already remove enough pain. OpenAI Secure MCP Tunnel materially reduces the reason to build a generic relay for OpenAI users who can access it, so a managed relay must be validated against actual residual demand before implementation.

The paid mechanism must never rely on a client-side secret embedded in an open-source application. Entitlement, if any, must be server-side.

## 7. Cash economics

Constraint: Tomas means **zero initial cash investment**, not zero time investment.

Important update: Tomas already has an Apple Developer membership, so Developer ID signing/notarisation does not introduce a new initial cash cost.

The project is being built primarily because Tomas needs it himself. Therefore conventional startup-scale market size is not required. The commercial overhead can be worthwhile with very few users; even one paying customer is meaningful if the incremental payment/service layer costs less than that customer's revenue.

Economic invariant:

> Do not activate fixed or recurring paid infrastructure before settled subscription revenue already covers that cost with a safety margin.

Where possible, use services with no fixed monthly fee at zero volume. Any managed infrastructure must have hard usage/rate/storage limits so one user cannot create unbounded cost. Each paid customer should cover payment-processing fees, their marginal infrastructure cost, a risk/support allowance, and profit.

The correct validation question is not 'is there a venture-scale market?' but:

> Is there at least one user of the open-source bridge willing to pay for a materially easier managed experience, and can that user be served profitably?

## 8. Support capacity

Earlier concern that one low-priced customer could consume excessive founder support time is reduced by the following concrete fact:

- Tomas has a cofounder across Vertinant activities who can take/delegate customer support initially.
- The intention is to use that capacity until recurring revenue is sufficient to hire dedicated support if needed.

This does not make support costless, but it means support bandwidth is not currently a single-founder blocker. The product should still have bounded support expectations, good diagnostics, self-service documentation, and an escalation playbook so subscription revenue does not implicitly buy unlimited bespoke consulting.

## 9. Market framing

Large-market proof is unnecessary for the decision to continue building because most of the core is justified by Tomas's own use.

There is category validation: OpenAI itself built Secure MCP Tunnel for local/private MCP connectivity, and multiple independent tools address LLM-to-local-repository workflows. That proves the connectivity problem exists but does not prove demand for this particular paid product.

The differentiated proposition should not be merely 'connect ChatGPT to Git'. Native tunnels, Codex, Claude Code, GitHub MCP and similar products make raw connectivity increasingly commoditised.

The stronger proposition is:

> Give an LLM a controlled, auditable, provider-neutral way to work against authoritative local Git repositories, with exact-base protection, local validation, constrained commits/pushes, and no arbitrary remote shell authority.

The commercial threshold remains deliberately small. A handful of users can justify the incremental managed layer if it stays operationally simple.

## 10. Immediate decision sequence

1. **Check CU ChatGPT permissions first.** Determine whether Tomas has, or can be granted, Developer Mode / Create custom MCP connectors in the CU Edu workspace.
2. If permitted, build the smallest MCP adapter over the existing bridge core and test it through OpenAI Secure MCP Tunnel against one disposable repository.
3. Compare that path with Drive on: setup friction, read/write capabilities, latency, local safety semantics, replay/recovery, admin dependencies, and usability in ordinary ChatGPT conversations.
4. If Secure MCP works well, make it the preferred OpenAI transport. Do not remove Drive until the CU/admin and provider-neutral fallback cases are understood.
5. Defer a custom paid relay until the residual convenience problem is demonstrated after native tunnel support is considered.
6. Keep the public product transport-pluggable and the free tier complete.

## 11. Open questions / do not silently assume

- Does Tomas's current CU ChatGPT role expose Developer Mode / custom MCP connectors?
- Would CU approve a custom local-Git MCP app even though it has not enabled the Git/GitHub app?
- Does Tomas's available OpenAI Platform organisation have Tunnels Read/Use and runtime-key access, or would that itself require another administrator?
- Is full MCP write/modify already enabled for Tomas's specific CU Edu workspace, or still unavailable in its rollout/policy configuration?
- Can the existing bridge transaction/replay model be cleanly mapped onto synchronous MCP tools without losing safety properties?
- After Secure MCP is available, is there enough residual cross-provider or zero-config demand to justify an LLM Git Bridge managed relay?
- Can the Google Drive fallback eventually move from full `drive` scope to `drive.file` through a different mailbox-ownership design?

## 12. Continuation checkpoint

When resuming this discussion, do **not** restart from the assumption that Secure MCP Tunnel makes `llm-git-bridge` obsolete. Treat the distinction as:

- **bridge core** = local Git policy/safety/execution layer;
- **Google Drive** = current transport/fallback;
- **Secure MCP Tunnel** = promising OpenAI-specific transport to test if CU permits custom MCP;
- **managed relay** = optional future commercial convenience layer, now lower priority until residual demand is known.

The exact next research/action question is whether the CU ChatGPT workspace grants Tomas Developer Mode / Create custom MCP connector access. That answer determines whether a Secure MCP prototype is immediately practical.
