# Conversation Harness Architecture and Migration Plan

Status: FINAL PLAN v2 — two adversarial audits incorporated
Date: 2026-09-28
Target repository: `krahd/llm-git-bridge`
Primary goal: make substantial work survive ChatGPT conversation timeouts, context exhaustion, browser failure, and deliberate handoff between conversations, without making any conversation itself authoritative.

This implementation is the infrastructure substrate for the existing **Conversational Work Continuity** research programme. It inherits that project's hard product invariant: Tomas should not perform bookkeeping merely to keep the system organised. Safety confirmations are allowed; manual tagging, hand-authored summaries, project assignment, graph maintenance, and continuation bookkeeping are not.

## 1. Executive design

The system should make **work persistent and conversations disposable**.

The implementation is deliberately two-stage.

1. **Stage 1 — Mac harness:** implement and prove the job state machine, continuation capsules, leases, handoffs, replay safety, and Safari re-entry beside the existing ChatGPT Shell Bridge v5 and `workspace.py`. ChatGPT continues to reach the harness through the already-proved Google Drive shell bridge. The Safari extension is an actuator, not an authority.
2. **Stage 2 — Cloudflare migration:** move the conversation-control plane to Cloudflare only after Stage 1 semantics are stable. Do not assume a generic HTTP, Cloudflare, GitHub, or custom ChatGPT connector. Preserve the proved Google Drive connector as the model-facing mailbox. A Cloudflare Worker ingests that mailbox and routes events to SQLite-backed Durable Objects. The Safari extension talks directly to Cloudflare. The Mac becomes an optional executor for local-only or Git operations, not the continuity authority.

The canonical authority split is:

- **GitHub:** canonical shared repository state.
- **Mac checkout/worktrees:** authoritative current local execution state whenever local execution matters.
- **Harness:** canonical workflow/job/continuation state.
- **Google Drive:** transport/mailbox, never repository or workflow truth.
- **Safari extension:** browser actuator and local UI only.
- **ChatGPT conversation:** replaceable reasoning session with no irreplaceable state.

## 2. Constraints that are treated as facts

### 2.1 Proved ChatGPT-side capabilities

The architecture may depend only on capabilities demonstrated in the current environment or already demonstrated by the existing bridge.

Proved:

- Google Drive connector: search/list/fetch; native Doc creation and editing; export; upload; and access to the existing bridge mailbox.
- ChatGPT Shell Bridge v5 through Google Drive, with pinned Drive IDs, durable request/result identity, crash semantics, and `health.json`.
- `workspace.py` durable Git workspaces and pushed checkpoint branches.
- ordinary public web access for documentation and research.

Explicitly **not assumed**:

- GitHub connector availability. A catalogue entry is not enough; the user states it is not enabled, and no GitHub execution surface is exposed in this environment.
- a generic HTTP request tool from ChatGPT.
- a Cloudflare connector.
- a custom MCP/plugin install being possible under the user's current ChatGPT administration.
- a documented ChatGPT URL that creates a new conversation and pre-populates/submits a prompt.
- stable private ChatGPT web APIs or stable DOM selectors.

### 2.2 Safari/runtime facts

A Safari Web Extension can use content scripts on explicitly permitted sites, background/service-worker code, extension storage, alarms, and browser tabs. Safari intentionally unloads nonpersistent background execution; state must therefore live in durable storage and event listeners/alarms rather than in-memory timers.

On macOS, Safari Web Extensions can use native messaging with their containing app. Stage 1 uses this as the default browser-to-harness path; loopback HTTP is explicitly deferred unless later portability work justifies it.

The extension must ask only for the hosts it needs, initially:

- `https://chatgpt.com/*`
- Stage 2 only: the exact Cloudflare harness origin, if the background extension ever talks to it directly. The preferred Stage 2 design keeps credentials in the native host and does not require page/content-script network authority.

The extension must never request `<all_urls>`.

### 2.3 ChatGPT UI automation is a weak boundary

OpenAI troubleshooting documentation explicitly notes that browser extensions can interfere with ChatGPT and that unusual browser configurations/automation may increase friction such as CAPTCHAs. Therefore:

- no cookie/token extraction;
- no private API interception;
- no network request rewriting;
- no hidden account/session manipulation;
- no broad DOM mutation;
- prefer visible, user-understandable UI actions;
- fail closed when ChatGPT UI structure cannot be positively identified.

## 3. Goals

The harness must support all of these without relying on prior chat transcript recovery:

1. Resume a named job in a new conversation.
2. Recover after ChatGPT "Message delivery timed out" without repeating ambiguous mutations.
3. Deliberately hand work to another conversation before context exhaustion.
4. Start or prepare a new ChatGPT conversation when a job explicitly enters `needs_agent`.
5. Prevent two conversations from mutating the same logical job concurrently unless explicitly allowed.
6. Preserve exact in-flight operation IDs across handoff.
7. Keep continuation state concise and semantic rather than storing entire transcripts.
8. Allow multiple independent repository jobs concurrently through existing `workspace.py` semantics.
9. Survive Mac/browser restarts in Stage 1.
10. Survive the Mac being asleep/unavailable after Stage 2 migration for jobs that do not require the Mac executor.
11. Cost $0 at the expected scale; Cloudflare migration must remain on the Workers Free plan and fail closed rather than incur paid overages.
12. Preserve the zero-administration product invariant: safety interactions may be explicit, but Tomas should not maintain tags, summaries, project mappings or continuation records by hand.

## 4. Non-goals

The first implementation must not attempt to:

- run an autonomous OpenAI API agent;
- scrape or reproduce entire ChatGPT conversations;
- bypass ChatGPT login, CAPTCHAs, rate limits, or human-verification systems;
- infer the model's remaining context window from undocumented UI internals;
- replace `workspace.py` Git safety;
- make Google Drive canonical state;
- make the Safari extension a source of truth;
- automatically merge/push repository changes outside the existing workspace integration gate;
- support iOS automation before macOS Safari semantics are proven;
- migrate Git execution off the Mac in the same project as conversation continuity.

## 5. Stage 1 architecture — Mac harness

```text
ChatGPT conversation
      |
      | proved Google Drive connector
      v
Google Drive shell mailbox
      |
      v
ChatGPT Shell Bridge v5
      |
      +------------------------------+
      |                              |
      v                              v
harness.py / harnessd.py        workspace.py
      |                              |
      v                              v
local SQLite                    job worktrees
      |                              |
      +---------------+--------------+
                      |
                      v
                 GitHub remote

Safari Web Extension
      |
      | native messaging
      v
macOS containing app
      |
      | user-only Unix socket / CLI adapter
      v
  harness service
```

### 5.1 Code placement

Source belongs in `krahd/llm-git-bridge`, preserving provider-neutral core semantics.

Proposed source layout:

```text
src/llm_git_bridge/harness/
    model.py            # state machine and validation; no transport assumptions
    store.py            # abstract persistence interface
    sqlite_store.py     # Stage 1 implementation
    service.py          # state transitions/business rules
    protocol.py         # request/response schemas and versioning
    local_protocol.py   # bounded local harness protocol over Unix socket

scripts/
    harness.py          # CLI entry point
    harnessd.py         # local harness service / Unix-socket daemon

safari/
    manifest.json
    background.js
    content.js
    popup.*
    README.md

spec/
    harness-protocol-v1.md
    transition-vectors.json
```

The installed runtime may mirror `harness.py` beside the current installed `workspace.py`, but the source remains in the repository and deployment is explicit.

### 5.2 Stage 1 persistence

Use Python standard-library `sqlite3`; do not introduce a database dependency.

Suggested state path:

```text
~/.local/state/chatgpt-shell-bridge/harness.sqlite3
```

Use WAL mode, foreign keys, explicit transactions, and migrations with a schema version table.

**Do not encode conversation ownership into the job lifecycle.** Job semantics and orchestration ownership are different dimensions. The first draft incorrectly mixed `claimed`, `working`, `waiting`, and `handoff_pending` into one status enum, which would make lease expiry silently rewrite semantic job state.

The database contains projections plus append-only events:

#### `jobs`

- `job_id` — stable restricted token; primary key.
- `version` — monotonically increasing integer for compare-and-swap.
- `title`.
- `goal`.
- `lifecycle` — `active | waiting | blocked | completed | failed | cancelled`.
- `waiting_on` — optional external dependency when lifecycle is `waiting`.
- `repo_url` — optional canonical repository URL.
- `repo_path` — local-private field only; stripped from portable/cloud/browser projections.
- `resource_key` — optional logical resource for collision detection.
- `workspace_job_id` — optional existing `workspace.py` job.
- `checkpoint_ref` and `checkpoint_sha` — optional.
- `next_action` — one bounded action.
- `success_condition` — verification gate for `next_action`.
- `blocked_on` — optional.
- `created_at`, `updated_at`.

Whether the job currently has an agent is derived from the lease/handoff tables; it is not a lifecycle value.

#### `leases`

- `job_id`.
- `lease_id` — random 128-bit identifier.
- `holder_id` — opaque session identifier, not a ChatGPT account identifier.
- `generation` — monotonically increasing fencing number per job.
- `acquired_at`, `expires_at`.
- `last_heartbeat_at`.

A lease is an orchestration lock only. **Lease expiry never proves that an external mutation stopped and never changes the job lifecycle.** Expiry only permits a later claimant to obtain a higher fencing generation.

#### `operations`

Records every stateful external action that may still need reconciliation:

- `operation_id` — stable idempotency identity.
- `job_id`.
- `kind`.
- `target`.
- `safety_class` — `read_only | idempotent | conditional | non_idempotent`.
- `submitted_at`.
- `status` — `planned | submitted | completed | failed | indeterminate`.
- `precondition` — commit SHA/version/token where available.
- `intended_postcondition`.
- `verification_surface`.
- `result_ref`.

A new conversation may claim an expired job but may not perform another mutation until every `submitted` or `indeterminate` operation has been reconciled.

#### `handoffs`

- `handoff_id` — random single-use nonce.
- `job_id`.
- `job_version` and `lease_generation` observed when issued.
- `status` — `pending | starting | consumed | expired | cancelled`.
- `issued_at`, `expires_at`, `consumed_at`.
- `consumed_by`.
- `launch_attempted_at` — optional browser actuator bookkeeping.

At most one live handoff per job. Consuming a handoff and creating the next lease occur in the same transaction.

#### `events`

Append-only audit record:

- monotonic event id;
- job id;
- job version;
- event type;
- operation/handoff/lease reference where relevant;
- timestamp;
- bounded JSON payload;
- optional actor class (`chatgpt`, `safari`, `mac_executor`, `system`, `human`).

Events are evidence; the current job row is the projection.

### 5.3 Continuation capsule

The continuation capsule is generated from authoritative harness state; it is not an independently editable second source of truth.

It should contain only:

```yaml
job: <id>
goal: <one paragraph>
status: <state>
constraints:
  - <important user/system constraint>
sources_of_truth:
  - <repo/ref/file/etc>
verified_completed:
  - <facts>
inflight_operations:
  - id: <id>
    state: <submitted|indeterminate>
    reconciliation: <exact next check>
checkpoint:
  workspace_job: <optional>
  ref: <optional>
  sha: <optional>
blocked_on: <optional>
next_action: <one bounded action>
success_condition: <verifiable condition>
```

No transcript, passwords, access tokens, cookies, private keys, or ephemeral signed URLs.

Hard size target: 32 KiB; hard protocol maximum: 64 KiB.

### 5.4 Core commands

The CLI is the stable local contract and the first thing ChatGPT uses through the shell bridge:

```text
harness create
harness show <job>
harness list
harness claim <job> --holder <opaque-id> [--handoff <nonce>]
harness heartbeat <job> --lease <lease-id>
harness release <job> --lease <lease-id>
harness operation-plan <job> ...
harness operation-submit <job> ...
harness operation-resolve <job> ...
harness checkpoint <job> ...
harness handoff <job> --lease <lease-id>
harness handoff-claim <job> <nonce> --holder <opaque-id>
harness wait <job> ...
harness block <job> ...
harness complete <job> ...
harness recover <job>
harness events <job>
```

Every state-changing command accepts `--expected-version` or equivalent and fails on stale state rather than overwriting.

Machine output is JSON by default; human-readable output is opt-in.

### 5.5 Integration with `workspace.py`

Do not merge the two state machines.

`workspace.py` remains authoritative for Git job/worktree/checkpoint/integration state. The harness stores only references to its job id and last verified checkpoint/integration evidence.

Rules:

1. A harness job that needs substantial repository mutation creates or resumes exactly one appropriate workspace job.
2. Before a context-heavy or browser-handoff boundary, checkpoint coherent repository work through `workspace.py` and record the pushed checkpoint SHA in harness state.
3. A harness lease expiring does not alter the workspace job.
4. Resumption first runs `workspace.py show` and reconciles its state with the harness reference.
5. Canonical completion remains commit -> push -> independent remote verification.
6. Harness `completed` is illegal for repository-changing jobs unless the canonical completion gate is recorded.

### 5.6 Local browser transport: native messaging first

The first draft preferred loopback HTTP. The adversarial review rejects that as the default because securely hiding a bearer credential from all extension contexts is subtle, CORS/host-permission behaviour adds another moving part, and Safari already gives us a containing macOS app.

Stage 1 therefore uses the official Safari native-messaging path:

```text
ChatGPT page content script
        |
        | browser.runtime.sendMessage
        v
Safari extension background/service worker
        |
        | browser.runtime.sendNativeMessage / connectNative
        v
containing macOS app extension
        |
        | local Unix-domain socket or direct CLI adapter
        v
harness service
```

Apple documents that content scripts cannot call the native app directly; this separation is desirable. The content script receives only the bounded UI data it needs. Secrets and local harness transport details stay in the native/background layer.

The harness service should prefer a Unix-domain socket under the user state directory rather than a TCP listener:

```text
~/.local/state/chatgpt-shell-bridge/harness.sock
```

Requirements:

- socket permissions restricted to the user;
- length-prefixed or newline-delimited bounded JSON protocol;
- 128 KiB request ceiling;
- no arbitrary shell endpoint;
- state mutations still require job version, lease generation and/or handoff nonce checks;
- containing app may invoke the CLI directly during the earliest prototype, but the final adapter should use one documented local protocol so CLI and browser behaviour are testable against the same service semantics.

A loopback HTTP adapter may be added later for Chrome/Firefox portability, but it is not part of the Stage 1 trust boundary.

### 5.7 Required Safari/native transport spike

Before building UI automation, prove all of:

1. content script -> background messaging works with access limited to `chatgpt.com`;
2. background -> native app request/response works with `nativeMessaging` permission;
3. native app can query the harness Unix socket/CLI and return a bounded response;
4. background unload/reload preserves non-secret state with `browser.storage`;
5. `browser.alarms` wakes the extension sufficiently for bounded checks while Safari is running;
6. a newly opened ChatGPT page can query for exactly one pending handoff without relying on unsolicited native push;
7. revoking ChatGPT site permission causes a clear fail-closed state.

Do **not** make app->JavaScript push a correctness requirement. Safari background execution is nonpersistent and iOS has additional native-messaging asymmetries. The durable trigger is the pending handoff in the harness; browser activation simply re-queries it.

## 6. Safari extension behaviour

### 6.1 Extension authority

The extension may:

- show jobs needing an agent;
- request a handoff nonce from the harness;
- open/navigate a ChatGPT tab;
- insert a bounded resume prompt;
- optionally submit it after explicit opt-in;
- detect a small set of known visible ChatGPT failure states for convenience;
- keep local `tab -> job -> handoff` associations.

It may not:

- mutate job semantics directly beyond claiming/acknowledging browser handoff events;
- contain Git logic;
- contain continuation truth independent of the harness;
- inspect authentication cookies/tokens;
- call private ChatGPT APIs;
- read unrelated conversations.

### 6.2 New-conversation procedure

There is no assumed stable pre-filled ChatGPT deep link.

The extension must use a versioned `ChatGPTPageAdapter`:

1. Open `https://chatgpt.com/` in a new tab, or focus an explicitly created blank ChatGPT tab.
2. Positively identify the current page as ChatGPT and identify a "new chat" control or a blank/new-chat state using accessible roles/labels plus conservative structural checks.
3. If an existing conversation might be active, do **not** type.
4. Locate the visible composer.
5. Insert the bounded resume prompt using normal DOM input events.
6. Verify the composer contains exactly the intended text.
7. Default Stage 1 behaviour: leave the prompt prepared and require one user click/keypress to submit.
8. After a separate acceptance gate, allow `auto_submit=true` for explicit harness handoffs only.
9. Observe the URL/tab change after first submission and save the opaque conversation URL locally only as navigation metadata, never as job authority.

Fallback on any adapter mismatch: show "Resume job X" in the extension popup with a copy-to-clipboard action. Never guess selectors and never type into an uncertain page.

### 6.3 Resume prompt

Keep the injected prompt minimal:

```text
Resume harness job <JOB_ID> using handoff <NONCE>.
Use the proved Mac Git Bridge. First read the harness job and reconcile any
in-flight operation before making a mutation. Continue from its recorded
next action; do not reconstruct work from this chat prompt.
```

The prompt contains no continuation body; the new conversation retrieves the authoritative capsule through the bridge.

### 6.4 Timeout recovery

A visible ChatGPT message-delivery timeout is only a browser/conversation failure signal.

If the current tab is attached to a harness job:

1. extension marks only a local UI fact: `delivery_failure_observed`;
2. it does not change any operation status;
3. it offers "Resume safely in new chat";
4. new chat claims a new handoff and first reconciles exact outstanding operation IDs;
5. no mutation is replayed merely because the old conversation failed.

Automatic timeout-triggered handoff is disabled initially. It may be enabled only after observed false-positive/loop testing.

### 6.5 Context exhaustion

Do not attempt to measure remaining model context from UI internals.

Supported mechanisms:

- explicit user button: **Continue in new conversation**;
- assistant-initiated semantic handoff: the current conversation checkpoints work, calls `harness handoff`, and tells the user/extension the handoff is ready;
- optional future visible marker observed by the extension, but marker parsing is convenience only and never the sole persistence path.

### 6.6 Harness-initiated conversation start

Mac Stage 1 can eventually support `needs_agent` events:

- conservative default: extension badge/popup shows a pending job when Safari is active;
- optional user setting: harness may open `https://chatgpt.com/` using the local OS when a single pending handoff exists;
- once the ChatGPT page loads, extension obtains the pending handoff from loopback and prepares the prompt;
- rate limit: at most one auto-launched tab per handoff id;
- no auto-launch for `blocked`, repeated failure, or expired handoffs;
- no repeated launch if a tab is already mapped to that handoff.

This feature cannot work while the Mac is asleep and should not pretend otherwise.

## 7. Job protocol and correctness rules

### 7.1 State transitions

Job lifecycle transitions are semantic and independent from conversation ownership:

```text
active  -> waiting
active  -> blocked
active  -> completed
waiting -> active
waiting -> blocked
waiting -> completed
blocked -> active          # explicit unblock/reopen action
active/waiting/blocked -> failed|cancelled
```

Lease/handoff transitions are separate:

```text
(no lease) -> lease(generation N)
lease N -> handoff pending
handoff pending -> consumed + lease(generation N+1)
lease N -> expired/released -> (no lease)
```

A lease expiring leaves `jobs.lifecycle` untouched. A handoff is not job progress; it is only an ownership-transfer mechanism.

Terminal lifecycle states are immutable by default. A future explicit `reopen` command may exist, but it must be a separately audited human-authorised transition rather than an accidental side effect of claiming a stale job.

### 7.2 Claim and fencing

Every lease has a monotonically increasing `generation`.

State-changing commands from a conversation include both `lease_id` and `generation`. A stale holder cannot mutate job state after a later claimant acquires a higher generation.

Long shell/Git operations remain governed by their own request/job IDs and external verification; fencing the harness does not kill them.

### 7.3 Idempotency

All side-effecting harness API requests accept an `idempotency_key` unique per logical action.

Store request hash + outcome. Reusing the same key with identical request returns the original result. Reusing it with different content is an error.

### 7.4 Time

Use UTC internally with RFC 3339 timestamps. Monotonic process clocks may be used for local elapsed-time checks but are not persisted as cross-restart truth.

Lease expiry comparisons use persisted wall-clock time and are advisory for ownership recovery; they never infer external side effects.

### 7.5 Event integrity and backup

Events are append-only through the service layer and updated in the same SQLite transaction as the job projection. Do **not** add a hash chain in the first implementation; it adds complexity without changing the principal local-user trust boundary. Add one later only if an audit demonstrates a concrete use.

SQLite backups use atomic backup APIs. Do not copy a live WAL database blindly.

Stage 1 has an unavoidable local single point of failure, so provide a **portable, sanitised recovery snapshot** on every handoff/terminal checkpoint and at a low-frequency periodic cadence. The backup is a recovery mirror, never a writable authority. It must:

- include job versions, lifecycle, leases/handoffs, unresolved operations, continuation fields and event sequence numbers;
- strip local-only paths and secrets;
- carry a schema/protocol version and SHA-256 digest;
- be written to the existing `Conversation Work Continuity` Drive area or another dedicated backup folder through the proved local Drive transport;
- be restorable only when the local harness is explicitly placed in recovery/read-only mode.

A failed backup must not roll back an already committed local state transition; it becomes an observable degraded-backup condition.

## 8. Security model

### 8.1 Main threat classes

- stale or duplicate conversation issuing a mutation;
- replayed handoff nonce;
- duplicate Drive file names;
- browser-extension compromise or over-broad permissions;
- prompt injection from arbitrary web content;
- malicious content included in continuation fields;
- stolen loopback bearer token;
- ChatGPT DOM changes causing input to wrong conversation;
- Cloudflare/Drive migration creating two simultaneous authorities;
- ambiguous external operations being replayed after timeout.

### 8.2 Defences

- compare-and-swap job version;
- fencing generations on leases;
- single-use expiring handoff nonce;
- operation ledger separate from conversation lease;
- idempotency keys;
- exact request IDs rather than Drive filenames as identities;
- content-size and schema bounds;
- least-privilege Safari host permissions;
- no Stage 1 network bearer token at all; native messaging + a user-only Unix socket form the local browser boundary;
- no arbitrary local shell endpoint;
- no secrets in continuation capsules;
- no automatic repository integration outside `workspace.py`;
- migration cutover flag guaranteeing exactly one harness authority;
- reconciliation required before a new holder may mutate if outstanding operations exist.

### 8.3 Prompt-injection boundary

The continuation capsule can quote user/project text but must distinguish **data** from **control fields**. Only typed harness fields (`next_action`, `operation`, etc.) drive state transitions. Retrieved webpage/file text can never directly issue a harness transition.

## 9. Stage 1 implementation phases

### Phase M0 — freeze protocol assumptions

Deliverables:

- `spec/harness-protocol-v1.md`;
- state diagram;
- transition JSON test vectors;
- capability matrix documenting proved/unproved integrations.

Gate: review finds no dependency on GitHub connector, generic HTTP from ChatGPT, private ChatGPT API, or Cloudflare.

### Phase M1 — pure state machine

Implement model/service in memory plus exhaustive transition tests.

Test:

- valid transitions;
- stale expected version;
- lease fencing;
- handoff single use;
- handoff expiry;
- illegal terminal completion;
- outstanding-operation reconciliation gate;
- idempotency-key reuse/mismatch;
- clock-skew/restart cases.

Gate: deterministic test vectors pass.

### Phase M2 — SQLite persistence

Implement migrations, transactions, WAL, recovery, backup, event audit, crash-boundary tests.

Fault injection at every transaction boundary.

Gate: kill/restart tests never produce a state impossible under the model.

### Phase M3 — CLI

Implement JSON CLI and integrate with shell bridge.

Live acceptance:

1. create job from ChatGPT through bridge;
2. claim;
3. record an operation;
4. handoff;
5. from another conversation, claim handoff and resume;
6. verify no duplicated operation.

Gate: successful cross-conversation recovery without past transcript.

### Phase M4 — `workspace.py` integration

Use a real disposable repository/test fixture and then one bounded real repository job.

Inject a conversation loss between shell request submission and acknowledgement. New conversation must reconcile request/result and checkpoint branch correctly.

Gate: no duplicate Git mutation and remote verification is preserved.

### Phase M5 — Safari/native transport spike

Prove the native-messaging + Unix-socket path and minimal permissions before any ChatGPT DOM automation.

Gate: background/native request-response survives background unload/reload, content scripts never receive secrets/local transport authority, and a ChatGPT page can query one pending handoff after navigation.

### Phase M6 — Safari manual re-entry

Implement popup:

- active jobs;
- pending handoffs;
- `Resume in new chat`;
- safe prompt preparation;
- copy fallback.

No auto-submit.

Gate: 50 manual resume cycles across browser restart/reload with zero wrong-tab insertions.

### Phase M7 — failure recovery

Test visible ChatGPT timeout, page reload during handoff, duplicate tab, expired handoff, browser crash, Mac restart, stale old tab, and changed DOM fixture.

Gate: every uncertain case fails closed or produces a recoverable pending handoff; no duplicate mutation.

### Phase M8 — optional automation

Only after M6/M7:

- optional auto-submit for explicit handoff;
- optional OS launch on `needs_agent`;
- conservative timeout detection.

Each feature independently toggleable and default-off until acceptance.

## 10. Stage 2 — Cloudflare control-plane migration

### 10.1 Critical rule

Do not move the harness authority to Cloudflare merely because Cloudflare can host state. Migration is complete only when **both** browser and ChatGPT have proved paths to the new authority without requiring the Mac.

No generic ChatGPT HTTP connector is assumed.

### 10.2 Model-facing transport: Google Drive remains

Use a **new dedicated Drive mailbox**, separate from the existing shell bridge:

```text
Conversation Harness/
    inbox/
    results/
    archive/
```

ChatGPT writes bounded request objects to `inbox` using the proved Google Drive connector. Cloudflare consumes them and writes acknowledgements/results. ChatGPT reads results through the same proved connector.

Drive remains transport only.

Do not share the existing Shell Bridge request folder between the Mac daemon and Cloudflare consumer; that would create competing consumers and ambiguous authority.

### 10.3 Cloudflare access to Drive

Preferred credential model: a dedicated Google service account shared only onto the `Conversation Harness` folder.

- Share the harness folder with the service-account identity, not the rest of Drive.
- Store service-account credential material only as Cloudflare Worker secrets.
- Use least Google API scopes compatible with listing, reading, creating, moving and deleting files in that shared folder.
- Never expose credentials to browser extension or ChatGPT.
- Rotate credentials and document revocation.

If service-account folder access is not viable in the actual Google account, use a private OAuth client/refresh token as a fallback, with the minimum required scope. Do not reuse the rclone shared-client credential that already carries a retirement warning.

This credential path is **unproved until exercised against the real Drive folder**. It is a migration prerequisite, not an implementation assumption.

### 10.4 Drive ingestion

Do not assume the Free-plan Scheduled Worker CPU budget is sufficient for repeated Google service-account JWT signing, token handling and mailbox parsing. Measure it, but design so it is not the critical constraint.

Preferred cloud mailbox architecture:

```text
Worker router
   |
   v
MailboxCoordinator Durable Object
   |  adaptive durable alarm (fast while active, backed off while idle)
   |  Google Drive API
   +----> Conversation Harness/inbox
   |
   +----> Job Durable Objects
```

Cloudflare Durable Object alarms are durable, at-least-once wakeups and have Durable Object request CPU semantics. The `MailboxCoordinator` owns one alarm at a time, catches downstream failures, records them, and explicitly schedules the next alarm so that exhausting the platform's automatic alarm retries does not permanently stop ingestion.

Before this design is accepted, a migration spike must prove:

1. a dedicated Google service account can access only the shared harness folder under the actual Drive account policy;
2. Cloudflare Web Crypto can create the Google service-account assertion within measured Free-plan CPU limits;
3. access-token reuse/caching prevents needless signing/token exchanges;
4. one bounded Drive list/read/write cycle fits the limits;
5. ChatGPT can produce the agreed mailbox request representation with the proved Drive connector **without the Mac**;
6. end-to-end request-to-result latency is low enough for an ordinary ChatGPT tool turn, not merely eventual.

Dedupe identity is request content `request_id` plus Drive file ID, never filename alone.

Processing shape:

1. bounded inbox list;
2. read candidate;
3. validate schema and size;
4. check `request_id` idempotency in coordinator/job state;
5. route to the named `Job` Durable Object;
6. Job Durable Object transaction applies or returns the prior result;
7. write result record to Drive with `appProperties` containing stable request/result identity where the API allows;
8. persist the result Drive file ID;
9. archive/move input;
10. on an ambiguous Drive write, query Drive by persisted file ID/appProperties before any retry.

The alarm is at-least-once by design. Therefore every ingest operation must be safe under replay.

Interactive latency is a correctness/usability gate, not a performance nicety. During an active ChatGPT handoff or recent mailbox activity, the coordinator should poll at the fastest cadence proven safe under quota/CPU tests (target 5–10 seconds). After a bounded quiet period it backs off. A conversation that cannot obtain a result within its normal bounded orchestration window must return a durable pending request ID and permit the next conversation to reconcile it; it must not busy-poll or resubmit.

### 10.5 Cloudflare state

Use two SQLite-backed Durable Object classes:

- one `MailboxCoordinator` singleton for Drive ingestion cursor/alarm/dedupe/health;
- one `Job` Durable Object per harness job for job semantics.

Worker responsibilities:

- HTTPS boundary;
- browser-device authentication;
- request size/schema boundary;
- routing to the correct Durable Object;
- no durable job truth of its own.

`MailboxCoordinator` responsibilities:

- Google credential/token cache metadata;
- bounded Drive polling;
- inbox/result file identities;
- cross-mailbox idempotency;
- durable alarm scheduling;
- ingestion health.

`Job` Durable Object responsibilities:

- job lifecycle projection;
- leases/fencing;
- handoffs;
- operation ledger;
- event log;
- idempotency for job commands.

Do not add D1, KV, R2, Queues or WebSockets initially. They solve no demonstrated Stage 2 requirement.

### 10.6 Browser-to-Cloudflare path

The Safari extension keeps the same content-script/background/native separation after migration. The native containing app becomes the credential broker for Cloudflare requests:

```text
content script -> background -> native app -> HTTPS -> Cloudflare Worker
```

Use a per-device random credential stored in macOS/iOS Keychain where supported. Do not place the long-lived device token in page-accessible DOM, the resume prompt, URLs, or ordinary extension storage if the native host can retain it instead.

The credential is:

- revocable;
- rotatable;
- scoped only to browser-actuator operations (`list resumable jobs`, `request/claim handoff`, `ack browser start`);
- incapable of executor/Git/admin operations.

TLS is mandatory. If native-host HTTP brokering proves materially unreliable on a target platform, a background-only HTTPS path can be reconsidered under a separate threat-model update; it is not silently substituted.

### 10.7 Cloudflare free-plan guardrail

As of 2026-09-28, official Cloudflare documentation states:

- Workers Free: 100,000 requests/day and 10 ms CPU per HTTP request;
- SQLite Durable Objects are available on Workers Free;
- Durable Objects Free: 100,000 requests/day and 13,000 GB-s/day;
- SQLite DO storage: 5 million rows read/day, 100,000 rows written/day, 5 GB total;
- exceeding a Durable Object Free-plan limit causes operations of that type to fail rather than become paid overage;
- Queues Free, if ever needed, includes 10,000 operations/day, but Queues are intentionally excluded from the initial design.

Policy:

- remain on Workers Free;
- do not enable Workers Paid as part of this project;
- fail closed at quotas;
- expose quota-health telemetry;
- alert before 50%/80% of any expected daily limit if practical;
- integration tests enforce bounded polling and payload sizes.

### 10.8 Cloud migration procedure

1. Export Stage 1 job state to a canonical portable JSON snapshot plus event stream.
2. Deploy Cloudflare implementation in shadow/read-only mode.
3. Run the same `transition-vectors.json` against local Python and Cloudflare implementations.
4. Mirror synthetic jobs to both implementations and compare state/event hashes.
5. Prove ChatGPT -> Drive -> Cloudflare -> Drive -> ChatGPT round trip without Mac.
6. Prove Safari -> Cloudflare -> handoff -> ChatGPT re-entry without Mac.
7. Freeze Stage 1 harness mutations.
8. Take final local snapshot.
9. Import to Cloudflare and verify counts/hashes/job versions.
10. Set a single explicit cutover epoch/id.
11. Switch extension base URL.
12. Mark local harness read-only archive.
13. Keep rollback snapshot until a defined acceptance period passes.

There must never be two writable harness authorities.

## 11. Relationship to the Mac after Cloudflare migration

The Mac remains an **executor**, not control plane.

A cloud job may indicate an executor requirement such as:

```json
{"executor":"mac-shell-bridge"}
```

If the Mac is asleep:

- job moves to `waiting` with a concrete dependency;
- conversation continuity still works;
- browser handoff still works;
- no one falsely claims repository/local execution completed.

Later work may let the Mac bridge consume executor tasks from the cloud harness, but that is a separate protocol and security review.

Moving Git execution itself into Cloudflare/GitHub APIs is explicitly outside this project.

## 12. Observability and operations

Every deployment exposes a bounded diagnostic summary:

- service version/protocol version;
- schema version;
- authority mode (`local-writable`, `local-readonly`, `cloud-writable`);
- active leases count;
- outstanding handoffs count;
- outstanding/indeterminate operations count;
- oldest unresolved operation age;
- last Drive ingestion timestamp in cloud stage;
- free-tier usage counters where obtainable.

Never expose continuation text, local paths, tokens, or private project content in unauthenticated health endpoints.

Provide:

```text
harness doctor
harness backup
harness export
harness import --dry-run
harness verify
```

## 13. Test matrix

### State-machine tests

- all legal transitions;
- every illegal transition;
- CAS failure;
- stale lease generation;
- lease expiry with no operation;
- lease expiry with submitted operation;
- lease expiry with indeterminate operation;
- duplicate idempotency key same payload;
- duplicate key different payload;
- double handoff claim;
- expired/cancelled handoff;
- terminal state immutability.

### Crash/fault tests

Kill process:

- before transaction;
- after projection update before event append (must be impossible if same transaction);
- after event/projection commit before response;
- before/after Drive request upload;
- after shell request STARTED before FINISHED;
- after workspace checkpoint commit before push acknowledgement;
- after Cloudflare Durable Object commit before Drive result write.

### Browser tests

- ChatGPT root opens blank/new state;
- existing conversation is never overwritten;
- selector fixture mismatch fails closed;
- composer insertion exactness;
- tab duplicated;
- tab closed mid-handoff;
- page reload;
- Safari restart;
- extension update;
- extension permission revoked;
- loopback/cloud unavailable;
- CAPTCHA/human verification appears;
- visible ChatGPT timeout;
- two pending jobs;
- repeated same handoff event.

### Security tests

- token absent/incorrect;
- content script attempts direct token access;
- malicious capsule text attempting state transition;
- content script attempting to bypass background/native mediation;
- oversized capsule/request;
- path traversal/job-id injection;
- SQL injection inputs;
- duplicate Drive filename;
- replay old nonce;
- stale old tab after new claim;
- local HTTP bound externally (must fail test);
- cloud cutover with local authority accidentally writable (must fail).

## 14. Acceptance criteria

### Stage 1 complete only when

- protocol/state machine is documented and tested;
- SQLite survives crash/restart tests;
- CLI cross-conversation resume works through the proved bridge;
- real Git job handoff preserves `workspace.py` checkpoint/integration semantics;
- Safari manual resume works with minimum permissions during the pilot; the final accepted workflow may auto-submit only explicit harness-generated handoffs after the wrong-tab/loop tests pass, so routine continuity does not become user bookkeeping;
- wrong-tab insertion count is zero across the acceptance run;
- timeout recovery does not replay ambiguous mutations;
- Mac restart preserves recoverability;
- portable sanitised Drive backup/restore is demonstrated and never becomes a second writable authority;
- repository change is committed, pushed, and independently verified on GitHub;
- installed runtime matches verified repository version;
- final adversarial security/correctness audit has no unresolved critical/high defects.

### Cloudflare migration complete only when

- same transition vectors pass local and cloud implementations;
- dedicated service-account access to the Drive harness folder is demonstrated under actual account policy;
- ChatGPT -> Drive -> Cloudflare -> Drive -> ChatGPT is demonstrated with Mac offline;
- Safari -> Cloudflare re-entry is demonstrated with Mac offline;
- cloud free-plan configuration is verified and no paid plan is enabled;
- one-and-only-one writable authority is verified;
- rollback snapshot exists and is tested;
- Mac executor unavailability degrades to `waiting`, not lost state;
- post-cutover audit has no unresolved critical/high defects.

## 15. Rollback

### Stage 1

Before deployment, back up existing installed bridge runtime. Harness is additive; bridge/workspace behaviour must remain independently usable if harness is disabled.

Feature flag:

```text
HARNESS_ENABLED=0|1
```

Disabling harness must not alter existing bridge request processing.

### Cloud stage

Rollback requires an explicit authority switch:

1. put cloud harness into read-only mode;
2. export cloud state/event log;
3. verify no unresolved cloud mutation is in flight;
4. import into local harness using version/hash checks;
5. switch authority epoch;
6. point extension back to loopback;
7. only then permit local writes.

Never "merge" two independently mutated job databases.

## 16. Documentation deliverables

Repository documentation should eventually include:

- `docs/conversation-harness.md` — user/admin architecture;
- `docs/conversation-harness-protocol.md` — wire/state schema;
- `docs/conversation-harness-security.md` — trust/threat model;
- `docs/safari-extension.md` — permissions, install, DOM-adapter policy;
- `docs/cloudflare-migration.md` — free-plan deployment/cutover/rollback;
- runbook for lost browser, lost Mac, lost Drive connector, stale lease, duplicate Drive event, and cloud outage.

## 17. Final adversarial audit 2 — brutal pass

The revised architecture was attacked again after audit 1. The following defects were found and corrected in v2:

1. **The document contradicted itself about Safari transport.** Early sections and diagrams still called loopback HTTP the preferred Stage 1 path after audit 1 had rejected it. All Stage 1 diagrams, permissions and code layout now consistently use Safari native messaging plus a user-only Unix socket/CLI adapter.
2. **Cloud mailbox latency was architecturally correct but operationally unusable.** A one-minute Drive polling interval can outlive an ordinary ChatGPT orchestration turn. The cloud coordinator now uses adaptive 5–10 second polling while active, then backs off, with durable pending request IDs when a result is not yet available.
3. **A browser handoff could be mistaken for a semantic job transition.** The final model makes handoff/lease state orthogonal to lifecycle; consuming a handoff only changes orchestration ownership.
4. **A stale conversation could still speak after lease rollover.** Every state mutation is fenced by lease generation and expected job version. External operations remain separately reconciled because fencing the harness cannot cancel a shell/Git mutation already submitted.
5. **Drive backup could accidentally become a second authority.** Recovery snapshots are explicitly sanitised, digest-bearing, read-only mirrors; restoration requires the live harness to enter an explicit recovery/read-only mode.
6. **Native messaging is not a magic security boundary.** The containing app and harness run as the same macOS user and are not a sandbox against compromise of that user account. The design claims least privilege and replay safety, not OS-level isolation.
7. **The zero-administration invariant conflicted with a permanently manual submit step.** Manual submit remains the pilot safety gate only. Auto-submit may be enabled solely for explicit harness-generated handoffs after acceptance testing; routine operation should not require Tomas to maintain continuity metadata or perform bookkeeping.
8. **Mac-offline Cloudflare migration still had an unproved ingress.** The migration remains blocked until an end-to-end ChatGPT -> dedicated Drive mailbox -> Cloudflare -> Drive result -> ChatGPT round trip is demonstrated with the Mac actually offline. No connector catalogue entry counts as proof.
9. **Cloudflare migration can create split brain.** Cutover requires an authority epoch, frozen local writes, import/hash verification, and explicit local read-only mode before cloud writes begin. Rollback reverses that sequence; databases are never merged after concurrent writes.
10. **Cloudflare Free is a constraint, not an entitlement.** Every cloud prerequisite is measured under the real account and current plan. If Google authentication, Durable Objects, alarm frequency or quota behaviour cannot be demonstrated on the free plan, migration stops and Stage 1 remains canonical.
11. **UI automation remains fundamentally brittle.** No amount of testing makes undocumented ChatGPT DOM stable. The architecture therefore treats prompt insertion/submission as replaceable convenience: a selector mismatch must degrade to a visible copy/resume action while all durable state remains intact.
12. **This is not yet autonomous task continuation while no ChatGPT session exists.** The harness can preserve, summon and re-enter work, but it cannot make the consumer ChatGPT product reason in the background. An OpenAI API agent runner would be a distinct later project with different cost/security assumptions.

### Residual risks deliberately accepted

- Stage 1 cannot initiate a new browser conversation while the Mac is asleep. Stage 2 exists specifically to remove that dependency.
- Google Drive is a critical transport dependency in both the existing bridge and the first cloud migration. A later transport can replace it because the harness protocol is provider-neutral, but adding another transport before the semantics are proven would increase failure modes.
- Safari/ChatGPT UI changes may temporarily disable automatic re-entry. This must never corrupt or lose work; it only degrades convenience.
- A compromised macOS user account can compromise Stage 1. The project is not an OS sandbox.
- Free-tier terms and limits can change. The migration runbook must re-verify them immediately before deployment.

### Final architectural verdict

The Mac-first implementation is the correct first version because it validates semantics using already-proved infrastructure. Cloudflare is a migration target, not a prerequisite. The migration should happen only after two independent proofs: (a) Stage 1 survives real cross-conversation failures without duplicate side effects; and (b) the dedicated Drive mailbox gives ChatGPT a Mac-independent, low-latency, free-plan path to the cloud coordinator. Until both are true, moving authority to Cloudflare would make the system more fragile rather than less.

## 18. Adversarial audit 1 — findings incorporated

This audit was performed against the initial draft before persistence. Material defects found and corrected:

1. **Job/lease state conflation — critical design error.** The draft encoded `claimed`, `working`, `waiting` and `handoff_pending` in one job status. Lease expiry could therefore accidentally rewrite semantic work state. Corrected by separating job lifecycle, lease ownership and handoff state.
2. **Loopback bearer isolation was underspecified.** Ordinary extension storage does not provide a strong enough conceptual boundary to promise that content scripts can never obtain a stored credential. Corrected by making Safari native messaging + a user-only Unix socket the Stage 1 default and removing the Stage 1 network bearer token.
3. **Cloudflare Cron CPU assumption was optimistic.** Free Worker HTTP/Cron CPU is 10 ms; Google JWT signing and mailbox parsing were not measured. Corrected by introducing a `MailboxCoordinator` Durable Object with durable alarms and requiring a measured credential/Drive spike before migration.
4. **Stage 1 had a local single point of failure.** Corrected with sanitised portable recovery snapshots to Drive at handoff/terminal checkpoints plus low-frequency backup, explicitly non-authoritative.
5. **Cloud migration assumed Google service-account folder semantics without proof.** Corrected by making real-account service-account access an explicit migration gate, with private OAuth fallback.
6. **Cloud result-write replay needed stronger identity.** Corrected by persisting Drive file IDs/appProperties and reconciling ambiguous writes before retry.
7. **Event hash chaining was premature scaffolding.** Removed from v1; SQLite transaction integrity and external checkpoints provide the required recovery value without another pseudo-security mechanism.
8. **Existing project context was missing.** This plan is explicitly an implementation substrate for the established `Conversational Work Continuity` research programme and inherits its zero-admin invariant.

Remaining known weak boundary after this audit: ChatGPT web-page automation itself. It is intentionally isolated behind a fail-closed adapter, manual-submit default, and copy fallback. No design choice can make undocumented third-party DOM stable; correctness therefore cannot depend on successful auto-submit.

## 19. External documentation used for design assumptions

Apple:

- https://developer.apple.com/documentation/safariservices/creating-a-safari-web-extension
- https://developer.apple.com/documentation/safariservices/managing-safari-web-extension-permissions
- https://developer.apple.com/documentation/safariservices/optimizing-your-web-extension-for-safari
- https://developer.apple.com/documentation/safariservices/messaging-between-the-app-and-javascript-in-a-safari-web-extension

Cloudflare:

- https://developers.cloudflare.com/workers/platform/limits/
- https://developers.cloudflare.com/workers/platform/pricing/
- https://developers.cloudflare.com/durable-objects/
- https://developers.cloudflare.com/durable-objects/platform/pricing/
- https://developers.cloudflare.com/durable-objects/best-practices/websockets/
- https://developers.cloudflare.com/durable-objects/api/alarms/
- https://developers.cloudflare.com/queues/platform/pricing/

OpenAI:

- https://help.openai.com/en/articles/7996703-troubleshooting-chatgpt-error-messages
- https://help.openai.com/en/articles/8184038-captchas-in-chatgpt

## 20. Immediate next implementation action

Do **not** start with the Safari extension or Cloudflare.

Start by adding the provider-neutral harness protocol/state-machine specification and deterministic transition vectors to `krahd/llm-git-bridge`, then implement the pure Python state machine with no transport dependencies. That creates a semantic oracle against which SQLite, Safari, Drive and Cloudflare adapters can all be tested. The implementation documentation should cross-reference, but not duplicate, the existing `Conversational Work Continuity` research project state.


## 18. Post-audit coexistence amendment (2026-09-28)

The production ChatGPT Shell Bridge v5 is not a predecessor that must be migrated away from. Harness v1 is an independent, opt-in continuity service and may coexist with v5 indefinitely. Existing conversations and bridge clients do not need migration.

To make coexistence fail-safe rather than conventional, v1 uses a separate runtime namespace:

```text
~/.local/state/chatgpt-conversation-harness-v1/
    harness.sqlite3
    harness.sock
    events/
    logs/
```

It must also use a distinct LaunchAgent label, PID/lock files, socket path, browser/native messaging identifier, and job namespace. Harness installation/deployment must never restart, replace, reconfigure, or write into the live `chatgpt-shell-bridge` runtime. Shared Git repository state is coordinated only through existing workspace/resource safety.

Rollout is therefore opt-in, not migration-driven: build and test harness v1 in parallel; pilot one or more new conversations/jobs; retain v5 unchanged as the stable path; expand usage only where the harness is useful. A future Cloudflare control plane remains optional and does not imply retirement of either local service.


## Browser transport correction — adversarial audit 3

Implementation testing invalidated one Stage 1 assumption in the earlier plan: a Safari native app extension is sandboxed, so treating it as if it could simply open the harness Unix socket under `~/.local/state` is not a sound default. Apple documents app groups for sharing data between a containing app and native extension. Harness v1 therefore keeps the Unix socket as the private CLI/control path and adds a separately authenticated HTTP endpoint bound only to `127.0.0.1:47653` for the WebExtension. Pairing is one-time and short-lived; browser bearer tokens are random and only their hashes are persisted. The extension requests only the exact loopback origin and `https://chatgpt.com/*`. It opens and fills a new conversation but deliberately does not press Send.

## Browser STARTING reservation

Browser re-entry is centrally fenced. A paired browser must atomically reserve the next pending handoff before it receives the resume prompt and nonce. The reservation changes the handoff from `pending` to `starting`, records the browser client and a short expiry, and hides it from other browsers. Repeating the request from the same browser returns the existing reservation without opening another conversation. If opening the ChatGPT tab fails the extension releases the reservation; if that release is lost, the short starting lease expires and the handoff returns to `pending`. The overall handoff expiry always wins. A conversation may claim either `pending` or `starting`; claiming clears the browser reservation and nonce secret while creating the next fenced work lease.

