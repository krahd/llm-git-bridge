# Conversation Harness v2 — extraction decision and historical directions

**Status:** IMPLEMENTATION EXTRACTED — historical pre-extraction directions preserved below
**Decision date:** 30 September 2026

## Extraction decision — 30 September 2026

Conversation Harness v2 is no longer an implementation subproject of `krahd/llm-git-bridge`. The dedicated private repository `krahd/conversation-harness` now owns v2 implementation and implementation-facing architecture.

This repository retains:

- Conversation Harness v1 as the local Mac/Safari reference implementation and empirical predecessor;
- the Shell Bridge/workspace coordinator as one executor/transport that v2 may use through an adapter;
- historical v1/v2 design evidence needed to understand the extraction.

The extraction was prompted by the widening problem boundary and by the real Safari acceptance pilot. In v1 build 0.1.4, Safari reliably opened the new ChatGPT tab but still failed to populate the prompt. Rather than make browser-extension behaviour the architectural centre, v2 treats browsers/providers as replaceable adapters beneath a durable provider- and conversation-agnostic work layer.

The v2 implementation now assumes:

- durable work identity above provider conversations;
- multiple Git repositories without a GitHub-specific core assumption;
- Git/documents/artefacts as semantic work state and a separate operational event/state store;
- provider capability negotiation, including optional private/undocumented acceleration behind safe fallback;
- a Rust/SQLite core bias;
- controlled browser/CDP integration where web-only provider capabilities require it;
- canonical research in `krahd/research/projects/conversational-work-continuity/`.

The earlier rule that v2 must wait for successful Safari prompt-fill acceptance is therefore superseded. v1 remains valuable reference evidence, but closing the Safari-specific actuator defect is not a prerequisite for v2 architecture or research.

## Historical directions preserved below

## Boundary

Conversation Harness v1 remains the deliberately small Mac/Safari implementation: a local durable state machine and daemon, an authenticated loopback browser API, explicit browser pairing, fresh-tab prompt fill, and manual Send. v2 must not be used as a reason to broaden or delay v1 acceptance.

This note records directions to revisit only after v1 is accepted in a real Safari/ChatGPT handoff pilot.

## v2 objective

Make conversation continuity independent of any one always-on Mac while preserving the v1 invariants: durable jobs, leases and fencing, explicit handoffs, at-most-once protocol mutation semantics, recovery from ambiguous delivery, least-privilege clients, and no automatic submission of ChatGPT prompts.

## Candidate topology

### Durable Internet relay/coordinator

Introduce a small HTTPS service as a durable rendezvous and coordination layer. Cloudflare Workers plus a durable-state primitive are a candidate deployment because they could make the coordinator reachable without requiring Drive, a particular desktop, or an inbound port on the user's network. Free-tier suitability must be re-evaluated against then-current limits before implementation; the architecture must not depend on a particular vendor.

The hosted component should be a narrow relay/coordinator, not a general remote shell. It should hold only the minimum durable coordination state needed to route handoffs, reservations, acknowledgements, and client presence. Repository execution remains delegated to explicitly paired executors.

### Optional Mac executor

The Mac becomes one executor rather than the coordinator's single point of availability. When online and authorised, it may execute repository or local-machine work and publish durable progress back through the coordinator. When offline, browser/mobile continuity and non-Mac executors should remain usable.

The existing Shell Bridge and Conversation Harness protocols should be adapted rather than replaced wholesale: preserve request IDs, operation identity, fencing generations, missing-delta recovery, and independent remote verification.

### Raspberry Pi or similar always-on local node

Support a low-power always-on local device as an alternative or complement to the hosted relay. It could act as a local coordinator, relay, executor for lightweight tasks, or secure bridge to other machines on the LAN.

The design should not assume a Raspberry Pi specifically. Any small Unix host should be able to implement the same executor/coordinator interface. A local node is particularly useful when users prefer self-hosting or want continuity while their primary Mac sleeps.

### iPhone client

Treat iPhone/iOS as an always-carried client surface, not as the durable daemon. iOS background execution constraints make it a poor place for an always-running coordinator, but it is well suited to:

- receiving handoff or approval notifications;
- opening/resuming the relevant ChatGPT conversation;
- displaying pending work and durable status;
- approving sensitive actions;
- initiating or routing a handoff to an available executor;
- providing a lightweight client for status, acknowledgements, and manual continuation.

The system must remain correct if the iPhone app is suspended, killed, offline, or delayed by the OS.

## Transport abstraction

v2 should separate harness semantics from transport. The same protocol-level operation should be able to travel through local Unix socket/loopback, an HTTPS relay, or a self-hosted node without changing job semantics.

A transport adapter should provide:

- authenticated client identity;
- scoped capability tokens with expiry/revocation;
- idempotent request delivery or explicit at-most-once reconciliation;
- acknowledgement distinct from operation completion;
- replay protection;
- bounded message sizes and timeouts;
- observable version/generation markers for recovery.

No transport should acquire ambient shell authority merely by participating in conversation continuity.

## Security and privacy constraints

- Keep repository credentials and local machine credentials on the executor that needs them.
- Prefer end-to-end or payload-level protection for relay-carried sensitive continuation material so a hosted relay can remain minimally trusted.
- Use short-lived, scoped, revocable client credentials rather than a single ecosystem-wide bearer token.
- Pair new devices explicitly and expose a clear revocation path.
- Preserve the v1 rule that browser automation may fill a prompt but does not press Send automatically.
- Do not expose handoff nonces, repository paths, or other private continuation details in unauthenticated status projections.

## Partition and recovery semantics

Design for ordinary failure rather than assuming uninterrupted connectivity:

- a relay acknowledgement is not evidence that an executor completed an operation;
- clients reconnect by reading durable state and computing the missing delta;
- expired reservations become reclaimable without duplicating completed work;
- concurrent executors are fenced by generation/capability, not by timing assumptions;
- an executor that returns after a partition must reconcile before resuming mutation;
- terminal job state remains immutable.

These should remain protocol invariants shared with v1, not application-level conventions.

## Migration path

1. Keep v1 operational and accepted as the local reference implementation.
2. Extract a transport-neutral harness client interface around the existing protocol/state model.
3. Add one relay transport behind that interface without removing local socket/loopback support.
4. Pair a second executor or client and verify cross-transport fencing/recovery.
5. Add mobile UX only after the relay semantics are proven independently of iOS lifecycle behaviour.
6. Preserve a self-hostable route so the hosted coordinator is optional rather than mandatory.

Existing v1 jobs should not require migration merely to keep working. v2 adoption should be opt-in and incremental.

## v2 acceptance criteria to define before implementation

At minimum, a future v2 plan should test:

- continuity while the primary Mac is asleep/offline;
- handoff between two independently paired clients through the relay;
- recovery after relay, client, and executor interruptions at each ambiguity boundary;
- duplicate/reordered delivery without duplicate semantic mutation;
- credential revocation and lost-device recovery;
- Raspberry Pi/self-hosted coordinator interchangeability with hosted relay semantics;
- iPhone suspension/offline behaviour without correctness loss;
- preservation of manual Send and least-privilege browser automation;
- migration/coexistence with v1 rather than a flag-day replacement.

## Explicit non-goals for the v1 closeout

Do not implement the hosted relay, Raspberry Pi support, iPhone app, multi-executor routing, or cross-device notification system as part of v1. v1 is complete only when its existing Mac/Safari path passes the real pending-handoff → fresh ChatGPT tab → prompt-fill → manual Send pilot and that result is recorded.
