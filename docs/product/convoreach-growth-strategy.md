# ConvoReach acceleration and visibility strategy

Status: active programme
Date: 2026-09-18

## Decision

ConvoReach is promoted from a future idea to an immediate prototype priority.

The objective is to test a potentially broader market while the shared Reach substrate is still small enough to shape around two real products. RepoReach remains Git-specific. ConvoReach remains conversation-specific. Shared infrastructure is limited to the already-frozen Reach seam.

## Fastest useful prototype

Do not begin with a generic multi-agent platform or API-created model instances.

The first ConvoReach prototype should attach two already-open native AI conversation tabs as addressable participants. A user should be able to:

1. attach conversation A and conversation B;
2. explicitly ask A to send a bounded addressed turn to B;
3. let B answer inside its existing native context;
4. return B's answer to A with provider/conversation/time provenance;
5. inspect, pause, detach and revoke either participant.

Initial implementation hypothesis: a Manifest-V3-style browser extension using explicit host permissions/content scripts plus extension messaging. A local/native companion or hosted Reach relay is added only when required by the experiment.

The prototype must preserve each provider's existing conversation as authoritative. It must not silently copy whole histories into a central transcript, manufacture API replacements for consumer conversations, or permit uncontrolled autonomous chatter.

## Prototype sequence

P0. Reconcile current Reach/RepoReach engineering state and identify the smallest reusable envelope/session code that can be consumed without importing Git semantics.

P1. Browser proof: attach two tabs in one browser and exchange one explicitly approved addressed message. Start with the smallest provider set that proves the mechanism; cross-provider support is the next gate, not a prerequisite for the first loop.

P2. Provenance and safety: stable local conversation handles, explicit participant consent, sender/recipient/correlation metadata, bounded payloads, turn limits, detach/revoke, and clear failure states.

P3. Cross-provider proof: one real ChatGPT/Claude/Gemini pair using existing native conversations. Adapter code is provider-specific; routing/session code is not.

P4. Reach reuse test: replace the local-only routing path with the shared Reach envelope/session seam where useful. Extract a shared package only if both RepoReach and ConvoReach now have real dependencies on the same code.

P5. External alpha: package the smallest installable extension/prototype and recruit a small cohort before broadening provider coverage.

## Growth principle

Visibility should be a product/system property, not a recurring personal obligation.

Automate artefact production and audience capture; do not automate unsolicited community participation.

### 1. Product-generated visibility

Every meaningful release should automatically update:
- a public changelog/release page;
- concise docs and examples;
- one or more reproducible demo scenarios;
- social/link preview metadata;
- an RSS/Atom feed;
- a stable permalink that can be shared manually or by approved integrations.

For ConvoReach, maintain a small set of high-intent demonstration pages generated from one canonical data file, for example:
- ChatGPT <-> Claude existing-conversation relay;
- ChatGPT <-> Gemini existing-conversation relay;
- Claude <-> Gemini existing-conversation relay;
- two independent ChatGPT conversations.

These pages must contain real supported behaviour and demos, not keyword-only programmatic SEO.

### 2. GitHub discovery

When the public repository exists:
- maintain accurate GitHub topics;
- keep README, social preview and release metadata current from canonical product metadata;
- publish a zero-cost GitHub Pages product/demo/docs surface automatically from the default branch;
- make install/demo/feedback entry points obvious;
- enable Discussions when an external community actually exists.

### 3. Conversion/capture

Every public surface should offer one low-friction next step appropriate to its maturity:
- try/install when usable;
- join alpha/beta before that;
- subscribe to release notes for passive followers.

Capture source attribution with coarse campaign/source codes so launches can be compared without invasive analytics.

Do not require an email merely to inspect a demo or read documentation.

### 4. Shareable product moments

Design the product so users can deliberately create safe-to-share artefacts:
- a synthetic/public demo transcript;
- a redacted screenshot/card;
- a small provenance diagram showing conversation A -> B -> A;
- a reproducible example.

Private conversation content is never public by default. Sharing is explicit and previewed.

### 5. Release syndication

Generate a release packet from canonical metadata:
- title/tagline;
- 2-3 sentence description;
- changelog highlights;
- screenshots/demo links;
- technical explanation;
- launch-specific variants.

Use that packet to reduce the cost of Product Hunt, Show HN, relevant directories, newsletters and social posts. Where a platform provides a legitimate API/scheduler, publication may be queued automatically; where community norms expect direct participation, automation stops at preparing the post.

Product Hunt currently supports scheduled launches and shareable scheduled pages, but asks makers not to solicit upvotes. Show HN requires something users can actually try and discourages marketing/PR copy. Treat those constraints as design inputs.

### 6. Referral loop only after utility

Do not add generic referral gamification before users receive value. Once a shareable ConvoReach result is genuinely useful, test a subtle attribution such as "made with ConvoReach" on explicitly public artefacts, with an option to remove it.

## Automation backlog

The first useful automation should be repository-native and cheap:

1. one canonical product metadata file;
2. static site/demo pages generated from it;
3. Pages deployment on approved release/default-branch changes;
4. generated release packet;
5. RSS/Atom changelog;
6. source-coded beta/install links;
7. lightweight aggregate funnel counts;
8. optional queued posting integrations only after the content pipeline is stable.

Avoid building a social-media bot, mass-DM system, auto-commenter, auto-upvoter or indiscriminate directory submitter. Those create platform risk and low-quality attention.

## Initial audience
