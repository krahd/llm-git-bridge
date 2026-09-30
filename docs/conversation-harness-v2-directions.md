# Conversation Harness v2 — deferred design directions

Status: DEFERRED DESIGN ONLY — do not expand v1 implementation scope
Date: 2026-09-30

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
