# Reach shared substrate — RepoReach/ConvoReach seam

Status: architecture design constraint
Date: 2026-09-18

## Purpose

RepoReach remains a Git-specific product. This document identifies the smallest infrastructure layer that should be domain-neutral so a future ConvoReach sibling can reuse proven connection machinery without copying RepoReach or turning RepoReach into a generic agent platform.

The goal is **minimal sibling, not literal fork**. A literal Git fork would duplicate networking/authentication code and create permanent divergence.

## Defining ConvoReach use case

A user in one existing AI conversation should be able to say, in effect:

> Ask my existing Claude/Gemini/other ChatGPT conversation what it has done and what it is doing, and bring the answer back here with provenance.

The conversations remain where they already live. ConvoReach should preserve their native histories, project context, provider tools and subscriptions rather than recreate them as API model instances or merge them into one synthetic context.

This use case is broader than cross-provider chat: two independent conversations in the same provider are also distinct participants.

## Shared Reach substrate

The shared layer may know about endpoints, sessions, messages and bounded payloads. It must know nothing about Git repositories or conversation semantics.

Responsibilities:
- opaque endpoint identity and local credentials;
- pairing and explicit product-scoped authorisation;
- short-lived sessions, rotation and revocation;
- outbound HTTPS/WSS connection and reconnect/backoff;
- domain-neutral outer envelope;
- message IDs, correlation IDs, hashes, expiry and acknowledgements;
- bounded chunk transfer;
- at-least-once delivery primitives and idempotency hooks;
- semantic-operation quota/entitlement hooks supplied by the product domain;
- sanitized connectivity/version diagnostics.

A minimal outer envelope can contain:

~~~json
{
  "reach_version": 1,
  "product": "repo",
  "message_id": "...",
  "endpoint_id": "...",
  "kind": "request",
  "correlation_id": null,
  "expires_at": "...",
  "payload_sha256": "...",
  "payload": {}
}
~~~

The relay routes this envelope. Product code interprets `payload`.

## RepoReach domain

RepoReach alone owns:
- repository discovery and path-free repository IDs;
- Git protocol-v2 request/result payloads;
- materialisation/snapshot semantics;
- exact-base and stale-state checks;
- repository/root policy and configured commands;
- worktrees and patch/blob application;
- validation;
- commits and push/current-branch authority;
- Git-specific replay/recovery;
- qualification-gated self-update semantics.

No shared Reach module should import or depend on these Git-domain components.

## ConvoReach domain

A future ConvoReach sibling would add instead:
- native-conversation endpoint identity;
- provider/session adapters for already-existing conversations;
- explicit room/participant membership;
- addressed message delivery and reply-to/correlation;
- provenance: source provider, conversation identity, time and originating message;
- bounded turn/round policies to prevent uncontrolled chatter;
- human-visible consent and revocation for each attached conversation.

It should not require a central synthetic transcript to replace the native conversations. A shared room is routing/provenance state, not the authoritative memory of each participant.

Generic autonomous agent orchestration, model selection, API-created debate rooms and endless self-triggering loops are outside this seam.

## Shared relay service

The hosted service can be domain-neutral internally from the beginning while remaining product-branded externally as RepoReach Relay.

Generic relay state:
- endpoints;
- product-scoped sessions;
- messages/results/events;
- bounded payload chunks;
- expiry/acknowledgement state;
- quota/entitlement counters;
- health/version metadata.

The relay must isolate product namespaces and authorities. A RepoReach session cannot silently gain ConvoReach authority, and a ConvoReach session cannot request Git operations.

The same physical Worker/Durable Object deployment could later serve both products, but this is an implementation option rather than a public umbrella-brand commitment.

## Metering

Shared infrastructure counts product-reported semantic operations, not low-level network traffic.

Examples:
- RepoReach: one accepted materialisation, edit/commit transaction or other defined core operation;
- ConvoReach: one delivered addressed conversation turn or another clearly documented unit.

Exact pricing and free allowances remain product-specific.

## Repository/package strategy

Do not create `reach-core` as a third repository merely because this document exists.

Near term:
1. keep the existing llm-git-bridge/RepoReach repository authoritative;
2. organise new relay/auth/envelope code behind an internal domain-neutral module boundary;
3. test that this module has no Git imports or repository assumptions;
4. keep RRR routing/account state domain-neutral where doing so costs little.

When ConvoReach is actually promoted from idea to prototype:
1. build the smallest conversation adapter against the internal seam;
2. measure real shared code;
3. only then extract the proven substrate into a tiny versioned shared package/repository if both products need independent release cycles;
4. pin compatible versions and keep product-domain tests separate.

This avoids both architectural duplication and speculative framework work.

## Minimal-fork acceptance test

The seam succeeds if a ConvoReach prototype can reuse endpoint identity, pairing, relay connection, envelope, reconnect, bounded payload, quota and diagnostics code while replacing all Git-domain modules with conversation-domain modules.

It fails if:
- ConvoReach must import Git/repository code to connect to the relay;
- RepoReach starts carrying conversation/room concepts in its Git core;
- product credentials are interchangeable across domains;
- the shared substrate becomes a generic remote-execution API;
- preserving reuse requires implementing multipath, agent orchestration or other speculative features that neither product needs.
