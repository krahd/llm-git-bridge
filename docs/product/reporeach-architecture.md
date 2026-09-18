# RepoReach architecture

Status: design freeze candidate
Date: 2026-09-18

RepoReach (RR) gives AI systems controlled access to Git repositories wherever those repositories live. RepoReach Relay (RRR) is the standard hosted route between supported AI surfaces and the local RepoReach daemon. The Git core remains transport-neutral internally, but the first consumer product deliberately standardises on RRR rather than asking users to choose or combine transports.

The existing llm-git-bridge implementation is the engineering predecessor of RepoReach. Its repository discovery, path-free public identity, exact-base transactions, constrained validation commands, controlled commits and pushes, replay handling, concurrency rules, current-branch authority, and structured self-update remain the foundation.

## 1. Product boundary

RepoReach is deliberately Git-specific.

In scope:
- local, GitHub, GitLab, Bitbucket, self-hosted, offline and other Git repositories;
- authoritative local working copies, including unpushed state;
- recursive discovery beneath operator-approved roots;
- read/materialise, edit, validate, commit, branch and policy-controlled push operations;
- multiple transport adapters terminating at one local transaction engine;
- RepoReach Relay as an optional managed transport.

Out of scope:
- general remote desktop or arbitrary computer control;
- arbitrary remote shell execution;
- making RRR a Git host or source of truth;
- moving Git credentials into the hosted service;
- allowing a hosted transport to weaken local repository policy.

Product sentence:

> RepoReach lets your AI work with your Git repositories, wherever they are.

## 2. Architectural invariants

1. Local Git remains authoritative.
2. Local RR policy is final; the relay requests authority but cannot grant it.
3. The local domain engine is transport-neutral and provider details stay outside Git semantics.
4. The first consumer product is RRR-first: one standard hosted route, not automatic multipath.
5. Correctness never depends on FIFO delivery, upload order or arrival time.
6. Duplicate RRR delivery is expected. Transaction identity plus canonical request bytes make retry/recovery idempotent.
7. Local filesystem paths and Git credentials never enter the public repository registry or relay account state.
8. The proven Drive/rclone transport remains in the codebase as a development/compatibility asset; whether it is exposed as a supported free consumer route is a later product decision.
9. Initial hosted deployment must require zero incremental cash and fail closed on free-tier exhaustion rather than create surprise billing.
10. A relay failure after a local commit is a publication failure, not a vanished commit.
11. Shared relay plumbing must remain domain-neutral enough that a conversation product can reuse it without importing Git semantics.

## 3. Logical system

~~~
AI / agent surface
        |
RepoReach web / tool adapter
        |
RepoReach Relay (RRR)
        |
shared Reach substrate
(identity, pairing, envelope, reconnect,
 quotas, diagnostics, bounded payloads)
        |
RepoReach Git domain
(discovery, policy, exact base, worktrees,
 validation, commit, optional push)
        |
authoritative Git repositories
~~~

RRR is the normal consumer route. The core keeps a transport boundary so an advanced or future adapter can be added without changing Git semantics, but simultaneous route arbitration, automatic failover, health scoring and cross-transport duplicate campaigns are not MVP requirements.

## 4. Core and relay protocols

RR preserves the existing protocol-v2 Git request semantics until a Git-core change genuinely requires a new version. The relay adds a small domain-neutral outer Reach envelope rather than rewriting Git semantics.

The outer envelope carries at minimum:
- Reach envelope version;
- product namespace (for example `repo`);
- globally unique message ID;
- opaque endpoint ID;
- message kind and optional correlation ID;
- SHA-256 of canonical product payload bytes;
- explicit expiry;
- bounded product payload.

For RepoReach, the product payload is the existing protocol-v2 Git request/result. The local daemon independently canonicalises and hashes that request. If the same transaction ID arrives with different bytes, it is rejected as an identity conflict. If identical bytes arrive again, the local replay system returns or republishes the durable result rather than executing the mutation again.

The outer envelope deliberately contains no repository, Git or conversation semantics beyond a product namespace. This is the seam that a future ConvoReach sibling can reuse.

## 5. Transport adapter contract

A transport adapter must:
- advertise or discover availability;
- receive bounded request envelopes;
- preserve transaction identity exactly;
- publish durable results;
- carry bounded snapshot/blob payloads where required;
- expose health diagnostics;
- never reinterpret Git policy.

The core distinguishes request state from transport state.

### Direct MCP

Where a client can reach a local or tunneled MCP service, an RR MCP adapter maps a constrained tool surface onto the core. MCP is a useful low-friction route when available, not a prerequisite.

### Mailbox transports

The current Google Drive/rclone mailbox remains a proven asynchronous route and compatibility path. It becomes one adapter among several rather than the product's installation model.

### Browser and site-tool surfaces

RRR can expose a normal authenticated web interface and, where supported, WebMCP site tools. Current ChatGPT desktop site tools can be discovered from an open webpage without a separately installed connector.

This does not imply universal reach. If an AI environment exposes no usable browser/tool surface, MCP/app capability, local execution channel, writable shared service or other bidirectional mechanism, there is no autonomous route to RR. Search-only public web retrieval is not a transaction transport.

Current OpenAI reference:
https://help.openai.com/en/articles/20001423-using-site-tools-in-the-chatgpt-desktop-app

## 6. RepoReach Relay

RRR is a thin, durable rendezvous and delivery service.

It may:
- identify a paired local endpoint;
- maintain authenticated browser/client sessions;
- queue bounded request envelopes while an endpoint is briefly unavailable;
- wake a connected daemon;
- relay short-lived payload chunks;
- retain enough state for unambiguous retry/recovery;
- expose browser, WebMCP, API and later MCP-facing surfaces;
- collect minimal aggregate operational counters.

It does not:
- clone or host user repositories;
- run Git against them;
- receive Git credentials;
- decide whether a repository may be pushed;
- retain source snapshots indefinitely;
- run model inference.

## 7. Zero-cash RRR MVP

The first hosted prototype should use only infrastructure that works without an incremental cash commitment:
- Cloudflare Workers Free for HTTPS/API routing and static assets;
- SQLite-backed Durable Objects for endpoint state, short queues, sessions and hibernating WebSockets;
- the provider's workers.dev hostname until a domain is justified.

As of 2026-09-18, Workers Free publishes a 100,000 requests/day limit. SQLite-backed Durable Objects are available on the free plan, support hibernating WebSockets and have a 5 GB aggregate storage allowance. Durable Object keys/values and SQLite rows are limited to 2 MB, so payloads must be chunked below that bound. These are dated implementation inputs to re-check before deployment.

References:
https://developers.cloudflare.com/workers/platform/limits/
https://developers.cloudflare.com/durable-objects/platform/limits/
https://developers.cloudflare.com/durable-objects/platform/pricing/

R2 is intentionally excluded as an MVP dependency. Its free allowance may become useful for large blobs, but its activation currently crosses a subscription/checkout boundary. The zero-cash prototype can use bounded Durable Object chunks until the core P12 blob contract and real usage justify another store.

## 8. Endpoint-oriented relay state

The simplest MVP uses one logical Durable Object per RepoReach endpoint. An opaque endpoint ID routes directly to that object, avoiding a global user/account database.

An endpoint object owns:
- device credential verifier and rotation state;
- active browser session identifiers/scopes;
- short-lived pairing challenges;
- the hibernating daemon WebSocket;
- request/result state machines;
- bounded payload chunks and expiry metadata;
- coarse rate/quota counters.

This supports a one-person/one-machine MVP without preventing an account layer later.

## 9. Pairing and authentication

The MVP should not require Google login, GitHub login or an external identity provider merely to pair one browser with one local daemon.

On first RRR enablement the local daemon generates an opaque endpoint ID and a high-entropy device credential stored in its private local state. It authenticates outbound HTTPS/WSS with that credential. Rotation and revocation are required before public beta.

The current runtime is deliberately standard-library-only, so the MVP should not introduce a cryptographic dependency solely for asymmetric device identity. A stronger signed-device design can be added later if its benefit justifies the dependency and migration.

Browser pairing:
1. local RR requests a high-entropy single-use pairing challenge with a short TTL;
2. RR opens or displays the RRR pairing URL;
3. the browser claims the challenge;
4. RRR notifies the authenticated daemon;
5. the user confirms the pairing locally, with matching-code or equivalent verification;
6. RRR issues a Secure, HttpOnly scoped browser session and invalidates the challenge.

A paired client may request an operation. It does not grant the operation locally. The daemon still applies repository identity, exact-base checks, allowed paths, configured command names, push policy, current-branch policy and all other core rules.

## 10. Relay lifecycle

RRR uses at-least-once delivery plus local idempotency rather than claiming exactly-once networking.

Recommended states:

~~~
accepted
 -> delivered_to_endpoint
 -> local_result_available
 -> result_delivered_to_client
 -> acknowledged
 -> deleted
~~~

Additional terminal states include expired, rejected and relay_error.

Rules:
- expiry is explicit;
- endpoint disconnect does not imply failure;
- local commit success and result publication remain separate facts;
- acknowledgement permits early deletion;
- absolute TTL guarantees eventual deletion;
- queue disappearance never implies success;
- ordering is never a correctness dependency.

Pairing TTL should be minutes. Transaction delivery TTL should be hours, not indefinite. Payload/result absolute retention should be no more than 24 hours for the MVP and should usually end earlier after acknowledgement.

## 11. Payloads and blobs

Control envelopes stay small. Larger snapshot/blob bytes are chunked separately and referenced by SHA-256.

MVP rules:
- content hash and byte count for every payload;
- chunks comfortably below the Durable Object 2 MB ceiling, for example 1 MiB;
- bounded aggregate payload per transaction;
- complete hash/length verification before local consumption;
- short TTL and early deletion;
- no cross-user deduplication that leaks content identity.

The independent P12 arbitrary-blob programme remains authoritative for eventual DOCX/PDF/image/audio/ZIP semantics. RRR carries that contract after it stabilises rather than inventing a competing blob model.

## 12. Data minimisation

Durable until revocation:
- endpoint ID;
- credential verifier/rotation metadata;
- active session identifiers/scopes;
- minimal entitlement state if paid service is introduced.

Short lived:
- pairing challenges;
- request/result envelopes;
- payload chunks;
- transient path-free repository display information needed by an active client.

Operational metadata should be limited to pseudonymous endpoint ID, operation class, byte counts, latency, relay error category, version/adapter and timestamps. Filenames, source content, local paths, command output, commit messages and model conversation content are not analytics fields.

Self-managed RR does not acquire mandatory telemetry merely because RRR exists.

TLS protects traffic to RRR. The architecture must not claim universal end-to-end encryption where a hosted browser/API/MCP surface necessarily exposes plaintext parameters to the relay. Stronger browser-to-device encryption can be added and described only where it is actually implemented.

## 13. Security requirements

Before public beta:
- high-entropy endpoint/session secrets;
- short-lived one-time pairing challenges;
- explicit local confirmation of new browser pairing;
- Secure/HttpOnly/SameSite browser sessions;
- CSRF protection for browser mutations;
- no side-effecting GET endpoints;
- appropriate origin/CORS restrictions;
- per-endpoint rate limits and bounded queue depth;
- strict message/aggregate payload limits;
- expiry and garbage collection;
- transaction ID + canonical hash collision checks;
- replay-safe result handling;
- no secret/local-path echo in errors;
- redacted logs;
- session/device revocation and credential rotation;
- fail-closed free-tier exhaustion.

A compromised relay must still not acquire arbitrary shell authority or the ability to bypass stricter local Git policy.

### Relay trust boundary

RRR is nevertheless inside the remote-request trust boundary. If RRR or an authenticated RRR session is compromised, an attacker could attempt any structured request that the local RR installation would ordinarily accept from that remote client. Local policy, exact-base checks, configured-command restrictions and push/current-branch gates bound that authority, but they do not prove that the human intended each request.

Therefore:
- documentation must not describe RRR as zero-trust or imply that a relay compromise is harmless;
- high-impact authorities remain explicit and default-off where the existing core already treats them that way;
- browser/session scopes should expose the minimum operation classes needed;
- revocation must stop future relay delivery promptly;
- a future optional local-confirmation mode for selected high-impact operations can further reduce relay/session compromise risk without changing the core transaction protocol.

## 14. Transparent multipath UX

"Transparent" means the user chooses repositories and semantic capabilities, not networking plumbing.

Normal status:

~~~
RepoReach: Connected
Repositories: 47
Read: enabled
Edit/commit: enabled
Push: enabled for approved roots
~~~

Advanced diagnostics may show:

~~~
Routes: Relay connected; MCP available; mailbox healthy
~~~

Normal setup should not ask users to choose "Drive vs MCP vs RRR" unless automatic connection fails.

## 15. First-release experience

1. Install RepoReach.
2. Choose one or more repository roots.
3. Choose semantic permissions: read, edit/commit and push; direct-current-branch authority remains advanced and explicit.
4. RR discovers repositories recursively and publishes path-free identities.
5. Enable RepoReach Relay and pair a browser once.
6. RR performs a safe round-trip verification.
7. UI reports Connected and repository count.
8. The user works from the AI surface; route details stay in diagnostics.

Direct transports can be enabled in addition to RRR when detected.

## 16. Failure semantics

- RRR unavailable: direct transports remain usable; local Git is unaffected.
- Local daemon offline: only bounded expiring requests are retained; RRR never claims execution.
- Duplicate path delivery: exact duplicate reuses durable result; conflicting bytes under one transaction ID are rejected.
- Stale base: local core rejects; relay does not rebase.
- Push failure after commit: durable local commit is preserved and push is reported separately.
- Free-tier quota exhausted: RRR fails clearly and closed, with no automatic paid spillover.
- Browser session lost: re-pair; repository state remains local.
- Suspected credential compromise: revoke/rotate sessions and endpoint credential without reinstalling repositories.

## 17. Open-source and hosted boundary

Free/self-managed RR remains complete:
- repository discovery;
- local transaction core and Git policy;
- validation commands;
- commit/push where locally enabled;
- protocol and diagnostics;
- self-managed/direct transports;
- no mandatory account or telemetry.

Hosted RRR sells managed convenience:
- zero-configuration relay operation;
- browser/site-tool surface;
- managed service reliability;
- later higher quotas, multiple endpoints, support or retained service-level audit metadata if users request them.

RRR-specific code may itself remain open source. Payment buys operation/convenience, not a secret protocol or crippled local edition.

## 18. Naming migration

Use RepoReach/RR and RepoReach Relay/RRR in new product documentation now. Do not immediately rename the repository, package, config directory, LaunchAgent, protocol identifiers or CLI while the independent P12-P15 programme is active.

After that programme reaches a stable integration boundary:
1. freeze a tested migration plan;
2. rename public repository/product surfaces;
3. make reporeach the primary CLI;
4. retain bounded compatibility aliases/state migration;
5. test upgrade from a real existing installation;
6. remove legacy names only after a documented deprecation period.

## 19. Implementation sequence after design freeze

1. Reach a safe integration checkpoint in P12-P15.
2. Confirm/extract a transport-neutral daemon adapter boundary without changing Git semantics.
3. Add an RRR client adapter behind an explicit feature flag.
4. Implement the minimal Worker + endpoint Durable Object relay with no billing and no R2.
5. Implement pairing, revocation, bounded queue/payload lifecycle and hibernating daemon WebSocket.
6. Add ordinary browser UI and WebMCP/site tools.
7. Run a disposable-repository live canary and cross-transport duplicate test.
8. Package roots, permissions, pairing and health verification into one guided setup.
9. Recruit external users before building billing or paid infrastructure.
10. Add additional adapters only where observed users need them.
11. Perform naming/package migration when concurrent bridge work is stable.

## 20. Prototype completion gate

A production-oriented prototype must prove:
- clean install and pairing without Drive or custom MCP;
- local Git policy remains authoritative;
- relay retries/reordering/duplicates preserve transaction semantics;
- daemon reconnect and Durable Object hibernation recover;
- session revocation works;
- TTL cleanup actually deletes payloads;
- quota exhaustion is safe;
- no local paths or credentials appear in relay state/logs;
- no fixed paid service is required;
- RRR plus another transport cannot duplicate a Git mutation;
- successful onboarding never requires understanding transport internals.
