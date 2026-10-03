# Unified Local Executor Bridge v6 — audited implementation handoff

**Prepared:** 2026-10-03  
**Purpose:** Single authoritative implementation plan for a fresh conversation to finish the unified bridge without re-designing, guessing, repeating ambiguous mutations, or relying on the user for shell/admin work.  
**Priority:** Restore a dependable production bridge first. Productisation and remote iPhone-side approval follow only after the bridge is operationally stable.

---

## 0. New-conversation operating contract

A fresh conversation must:

1. Read this file first.
2. Use the `persistent-adversarial-executor` and `mac-git-bridge` skills.
3. Treat GitHub as canonical repository state, the Mac checkout as authoritative current local execution state, and Drive as transport only.
4. Resume the existing hardening branch; do not create a competing implementation branch unless the branch has diverged or become unsafe.
5. Before every mutation, reconcile only the exact volatile state that can invalidate it.
6. Submit each bridge request once with a unique ID and non-empty `explanation`.
7. Never busy-poll. After one exact result check, if a request is still pending, retain its ID and stop probing that request in that execution slice.
8. Never replay an ambiguous mutation. Inspect Git/filesystem/remote state and execute only the missing delta.
9. Keep each bridge command comfortably under the 300-second request ceiling. Split test suites and use CI for the complete suite.
10. Do not ask the user to type shell commands. The user may be asked only for an explicit approval/rejection action when the security model genuinely requires human authorization.
11. Do not weaken approval semantics to achieve automation.
12. Do not touch the Conversation Harness service; it is a separate product.
13. Do not deploy laurenzo.net until this bridge is operationally repaired and accepted.

If an implementation detail conflicts with this plan, inspect current source/tests and authoritative state. Do not invent a new architecture silently. If a genuinely new material constraint appears, persist it in this file/project state before changing direction.

---

## 1. Verified current state

### Repository

Repository:

`/Users/tom/tom-repos/projects/llm-git-bridge`

Canonical remote:

`https://github.com/krahd/llm-git-bridge.git`

Verified canonical `main` / `origin/main`:

`ebe631c93d4cd3f97495bea8200c8c208fa3bc37`

Commit message:

`Add foreground operator approval broker`

Existing durable hardening branch:

`ai/installer-approval-integrity-20261003-a1`

Verified local hardening head:

`eb0b54b958422527196b33c49707230c151ded26`

The immediately preceding bridge request `bridge-swift-sdk-fix-20261003-1436-114` independently pushed and verified the remote hardening branch at this same SHA.

Do not assume these refs remain current when a new conversation starts. First reconcile:

- local branch/head/status;
- `origin/main`;
- remote hardening ref with `git ls-remote` in a network-enabled repository-scope request.

Preserve unrelated local work.

### Existing hardening work already implemented on the branch

The branch includes, in order, the integrated approval broker plus additional staging/cutover hardening and a persistent native menu-bar approval client.

Known branch commits after canonical main include work equivalent to:

- bind approval helper into staged install integrity;
- bind approval app path to actual install directory;
- verify staged approval integrity before cutover;
- harden staged bridge cutover transaction;
- close bridge staging and cutover integrity gaps;
- add persistent menu-bar approval client;
- pin explicit macOS SDK for approval GUI build.

The exact branch history must be read from Git before new mutation; commit labels above are orientation only.

### Tests already verified on `eb0b54b...`

The SDK-fix request completed successfully:

- native Swift approval app compilation: passed;
- approval app self-test: passed;
- `bash -n` on installer/cutover: passed;
- 30 focused installer/cutover/approval tests: passed;
- hardening branch push + remote verification: passed.

Do not rerun these merely for reassurance. Rerun them only after relevant source changes.

### Disposable stage rehearsal

The previous real stage-only rehearsal found a genuine missing explicit macOS SDK in the Swift compile invocation. That defect was fixed in `eb0b54b...`.

A second rehearsal payload was prepared in conversation but was **not durably submitted** before transport reset. A new conversation should use a fresh request ID and rerun the rehearsal after current-state reconciliation.

### Production v5 mailbox

Human path:

`ChatGPT Shell Bridge`

Remote:

`chatgpt-git-bridge:`

Drive root folder ID:

`1PRsQHgVsgXhIYT_8akGFRGdhdlw9Lmk6`

Requests folder ID:

`1jvTV0g4JVypFJ5olOovIPtCRIlawl4MP`

Results folder ID:

`1SMdHzWEj2w2zRcOsCBc6fGT44ltco1fL`

State directory:

`/Users/tom/.local/state/chatgpt-shell-bridge`

Allowed root:

`/Users/tom/tom-repos`

### Loaded service topology — IMPORTANT

At the latest authoritative inspection, these services were loaded:

- `com.tom.chatgpt-shell-bridge` — loaded
- `io.llm-git-bridge.daemon` — loaded
- `net.laurenzo.local-executor-bridge-v6-staging` — loaded
- `net.laurenzo.chatgpt-conversation-harness-v1` — loaded
- `net.laurenzo.local-executor-bridge` — **not loaded**

Critical finding:

`com.tom.chatgpt-shell-bridge` and `io.llm-git-bridge.daemon` both launch the **same installed v5 `bridge.py` with the same production config and the same production mailbox**.

Therefore there are currently two loaded consumers of the production v5 mailbox. Do not infer safety from a single health publisher. The final cutover must quiesce and retire both legacy labels transactionally.

The v6 staging bridge is isolated and safe to preserve during development. It uses a separate mailbox:

Base path:

`Local Executor Bridge v6 Staging`

Drive root folder ID:

`15ql2yACOq7H6qo0IgzosySqW8nHgUKXv`

Requests folder ID:

`101ZPUf3ZLCG2BW8HLgQazg48TNyUMrNa`

Results folder ID:

`1xnU55UNAc-6APc6b-8naPNyk4DlUC3xd`

State directory:

`/Users/tom/.local/state/local-executor-bridge-v6-staging`

Installed staging runtime:

`/Users/tom/.local/share/local-executor-bridge-v6-staging`

Config:

`/Users/tom/.config/local-executor-bridge-v6-staging/config.json`

This isolated staging bridge is valuable as a control plane for production cutover and must remain loaded until production v6 has passed final acceptance.

### Installed runtime caveat — both loaded bridge builds still use the broken modal path

Do **not** assume that the durable approval protocol on the hardening branch is already deployed.

Latest installed-code inspection proved:

Production installed runtime:

`/Users/tom/.local/share/chatgpt-shell-bridge/bridge.py`

SHA-256:

`10e0654a230abd04f1a715342f144a62588373e4cd072f36336d728ad761ae8d`

It still implements JXA/AppKit `NSAlert.runModal`.

Isolated v6 staging runtime:

`/Users/tom/.local/share/local-executor-bridge-v6-staging/bridge.py`

SHA-256:

`7fad469760e5c3eafcc8b3a61afd299c99bc6d25ee50fbfc61f2e56a358084d9`

It also still implements JXA/AppKit `NSAlert.runModal`.

Therefore neither currently deployed daemon can consume the new durable pending/decision approval records. The recovery plan must first bootstrap the isolated staging runtime to the audited candidate before using staging as the durable approval/cutover controller.

The current classifier inspection also proved that `/usr/bin/open` is **not** in the installed high-impact or non-repository mutation classifier. This makes LaunchServices a plausible bootstrap escape from the bridge sandbox, but implementation must still qualify it with a harmless launch canary before relying on it.

### Historical approval failure

The production v5 bridge currently fails closed on high-impact requests because its old modal UI path is unreliable. Repeated SSH/system requests were rejected before execution after confirmation timeout.

Exact diagnostic result:

`bridge-dialog-diagnose-20261003-011`

Terminal state:

- `status: rejected`
- `operator confirmation timed out; request was not started`

Thus the queue/request path is not the principal defect. Approval presentation is.

---

## 2. Non-negotiable architecture

### 2.1 One production execution daemon

Final production service label:

`net.laurenzo.local-executor-bridge`

It owns:

- one production Drive mailbox;
- request validation and deduplication;
- policy classification;
- durable request journal;
- active child-process supervision;
- approval state machine;
- shell execution;
- Git workspace coordinator entrypoint;
- health publication;
- wake lease;
- final result publication.

Shell and Git are capabilities of this one daemon. The historical semantic Git protocol remains compatibility code only; it must not remain a second production consumer.

### 2.2 Approval UI is a client, never a second executor

The native Mac app is a separate human-control-plane process.

It may:

- display pending approvals;
- display bridge health/status;
- display exact action, cwd, category, explanation and authority;
- approve exactly one challenge;
- reject exactly one challenge;
- show recent approval audit records;
- offer a stop-accepting-new-work control only through a narrow daemon-supported mechanism later.

It must **never**:

- execute arbitrary commands;
- edit the command it is approving;
- broaden filesystem/network/Git authority;
- silently approve future commands;
- act as another Drive mailbox consumer.

### 2.3 Browser/device origin is irrelevant

A request submitted from:

- Safari on the Mac;
- Firefox on the Mac;
- Safari on the iPhone;

must enter the same production bridge protocol.

For v6 operational acceptance, the human approval may occur on the Mac approval app regardless of request origin. iPhone-side approval is a later client of the same protocol and is not allowed to block restoration of the working bridge.

### 2.4 Fail closed

Any failure in:

- policy classification;
- approval challenge generation;
- challenge integrity;
- decision validation;
- expiry;
- GUI launch;
- LocalAuthentication step-up;
- durable journal write;
- sandbox setup;
- audit logging;

must reject or keep the request pending safely. Never downgrade automatically to `off`, generic modal approval, or unrestricted shell.

---

## 3. SOTA / best-practice decisions incorporated

These are implementation requirements, not optional commentary.

### 3.1 Separate background service from UI

Apple ServiceManagement guidance distinguishes background agents from UI/login items. Keep the daemon independent of the GUI so service operation does not depend on the app window being present.

Reference:

- Apple `SMAppService`: https://developer.apple.com/documentation/servicemanagement/smappservice
- Apple DTS guidance on separate agent + UI: https://developer.apple.com/forums/thread/791948

Immediate v6 recovery may keep the existing audited LaunchAgent/cutover mechanism to reduce migration risk. After production v6 is accepted, a subsequent bounded service-management phase should package/register the app/agent with `SMAppService` rather than continuing to hand-manage plists indefinitely.

Do not mix that modernisation into the recovery cutover unless it can be proven independently first.

### 3.2 Menu bar is convenience, not the sole approval surface

Apple notes status items are not guaranteed to be available at all times. Therefore:

- pending approvals are durable outside UI state;
- closing/hiding the window does not cancel them;
- the app must be launchable directly and show the pending queue even if the status item is hidden;
- an optional notification may announce pending work but carries no authority.

References:

- `NSStatusItem`: https://developer.apple.com/documentation/appkit/nsstatusitem
- `NSStatusBar`: https://developer.apple.com/documentation/appkit/nsstatusbar

### 3.3 Event-driven queue watching

Replace one-second-only polling with a `DispatchSource` filesystem watcher on the pending-approval directory. Keep a low-frequency fallback rescan for robustness if desired.

Reference:

https://developer.apple.com/documentation/dispatch/dispatchsourcefilesystemobject

### 3.4 Transaction-specific authorization

Adopt "what you see is what you sign":

The UI must show the significant action data and the daemon must bind approval to the exact action.

References:

- OWASP Transaction Authorization Cheat Sheet
  https://cheatsheetseries.owasp.org/cheatsheets/Transaction_Authorization_Cheat_Sheet.html
- OWASP AI Agent Security Cheat Sheet
  https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html

Approval must be:

- exact-action-bound;
- short-lived;
- unique;
- replay-resistant;
- invalidated by any payload change;
- checked again immediately before execution.

### 3.5 Local step-up authentication for high-impact actions

For categories with external/system/destructive effect, `Approve once` must use a fresh `LAContext` and `deviceOwnerAuthentication` (or the stricter policy chosen by tests/config) before writing `allow`.

Do not cache a prior successful authentication for later approvals.

Reject does not require authentication.

Reference:

https://developer.apple.com/documentation/localauthentication/lacontext

The `localizedReason` must describe the actual consequence clearly and concisely.

### 3.6 Secure Enclave-backed approval signatures

For high-impact approvals, the preferred v6 design is stronger than trusting a same-user decision file alone.

On first approved setup, the native approval app should generate a 256-bit elliptic-curve private key protected by the Secure Enclave/Keychain:

- `kSecAttrTokenIDSecureEnclave`;
- `kSecAttrAccessibleWhenUnlockedThisDeviceOnly`;
- `.privateKeyUsage`;
- `.userPresence` (or a stricter current-biometry policy if chosen deliberately).

The private key never leaves the Secure Enclave. Export only the public key and pin its fingerprint/public representation in bridge approval configuration/state.

When the user chooses `Approve once`:

1. the GUI re-reads and revalidates the pending challenge;
2. it signs the canonical challenge bytes/hash with the protected private key;
3. Secure Enclave/Keychain access requires user presence;
4. the decision record contains the signature and signer/public-key identifier;
5. the Python daemon verifies the signature against the pinned public key before accepting `allow`.

This prevents an arbitrary same-user process from forging an `allow` file merely because it can write to the state directory. The LLM-controlled request process never receives the private key.

If the Mac cannot create/use a Secure Enclave key, fail closed for high-impact approvals unless an explicitly tested fallback policy has been configured. Do not silently fall back to unsigned `allow`.

References:

- Apple, Protecting keys with the Secure Enclave:
  https://developer.apple.com/documentation/security/protecting-keys-with-the-secure-enclave
- `kSecAttrTokenIDSecureEnclave`:
  https://developer.apple.com/documentation/security/ksecattrtokenidsecureenclave
- `SecAccessControlCreateFlags`:
  https://developer.apple.com/documentation/security/secaccesscontrolcreateflags

### 3.7 Atomic/durable approval records

Pending/decision/audit writes must be atomic and symlink-safe.

Requirements:

- directories `0700`;
- files `0600`;
- reject unsafe request IDs;
- reject symlink path components / decision targets;
- write temp file in same directory;
- flush file;
- atomic replace/rename;
- optionally fsync containing directory where practical;
- never modify an existing approval record in place.

### 3.8 Higher-level IPC is preferred long-term

Apple recommends higher-level IPC such as XPC over raw Mach IPC.

Reference:

https://developer.apple.com/documentation/xcode/conforming-to-mach-ipc-security-restrictions

Because the execution daemon is currently Python and the durable file challenge protocol is already implemented/tested, do **not** block recovery on a Python↔XPC rewrite. Harden the file-based one-way approval protocol for v6. Treat native XPC as a v6.1/v7 migration when the execution service is packaged natively enough to authenticate peers cleanly.

### 3.9 CI must use a real macOS temp environment

The repository has no current GitHub Actions workflow.

The full Git-heavy suite uses Python `tempfile` extensively. The previous attempt to force `TMPDIR` inside the repository contaminated repository discovery and generated false failures.

Therefore:

- focused tests run locally through the bridge;
- complete suite runs on disposable macOS CI where `/tmp`/system temp works normally;
- never "fix" production repository discovery to accommodate repo-local test temp directories.

GitHub currently supports standard macOS hosted runner labels including `macos-latest`, `macos-15`, and `macos-26`.

Reference:

https://docs.github.com/en/actions/reference/runners/github-hosted-runners

Use a stable non-preview runner, initially `macos-15` unless implementation-time official docs show it has been deprecated. Do not use preview `xcode-*` runners for the release gate.

### 3.10 CI supply-chain policy

Use official GitHub actions only.

At implementation time:

- resolve the current official `actions/checkout` and `actions/setup-python` stable releases;
- pin them by **full immutable commit SHA**, not a floating tag;
- record the release/tag and resolved SHA in a comment in the workflow;
- `permissions: contents: read`;
- `persist-credentials: false`;
- no repository secrets in this test workflow.

Current research found `actions/checkout` v5.x and `actions/setup-python` v7.x as current families, but the implementation conversation must resolve the full current immutable SHAs rather than guessing them.

### 3.11 Distribution hardening is a separate post-recovery gate

For Tomas's local machine, ad-hoc signing is acceptable for the immediate bootstrap/acceptance candidate.

Before public downloadable distribution:

- Developer ID sign;
- Hardened Runtime;
- notarize;
- staple;
- verify Gatekeeper;
- minimize entitlements.

References:

- https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution
- https://developer.apple.com/documentation/security/hardened-runtime

Do not delay restoration of the local bridge on Developer ID/notarization work.

---

## 4. Approval protocol v6

### 4.1 Durable request state machine

Refactor the current synchronous approval wait into a non-blocking durable state machine.

States:

`RECEIVED`
→ `VALIDATED`
→ either `READY_TO_START` or `AWAITING_APPROVAL`
→ `APPROVED`
→ `STARTED`
→ `FINISHED`

Terminal alternatives before `STARTED`:

- `REJECTED`
- `EXPIRED`
- `INVALID`

Crash ambiguity:

`STARTED` without durable `FINISHED` is `INDETERMINATE`; never replay automatically.

An approval wait must not consume an execution worker slot.

Health must expose at minimum:

- `active_requests`
- `pending_approvals`
- `started_without_finished`
- approval GUI/config mode
- bridge version/instance ID
- production mailbox IDs
- update timestamp

### 4.2 Approval challenge schema

Version the schema explicitly.

Required immutable fields:

- `schema`
- `kind = operator_approval_request`
- `request_id`
- `bridge_instance_id`
- cryptographically random `nonce` with at least 128 bits of entropy; use 256 bits by default
- `payload_sha256`
- `category`
- `explanation`
- `cwd`
- exact command text
- requested write scope
- effective write scope
- network authority boolean
- structured authority/effect summary
- `created_at`
- `expires_at`

`payload_sha256` is computed by the daemon over one canonical immutable projection. Use one documented Python canonical serialization and a golden-vector test. The GUI does not need to be trusted to recompute it; it displays and echoes the daemon-provided fingerprint.

Any payload change requires a new challenge/nonce.

### 4.3 Decision schema

Required:

- `schema`
- `kind = operator_approval_decision`
- `request_id`
- `bridge_instance_id`
- `nonce`
- `payload_sha256`
- `decision = allow|cancel`
- `decided_at`
- `client = mac_gui`
- `signer_key_id`
- `signature_algorithm`
- `signature_b64`
- step-up result metadata that is informational only

Daemon validates:

- safe request ID;
- pending record exists;
- same instance ID;
- same nonce;
- same payload hash;
- not expired;
- not previously consumed;
- decision file is regular, owned appropriately and not a symlink;
- request has not changed;
- signature algorithm is the configured approved algorithm;
- signer key ID matches the pinned local approver key;
- Secure Enclave-backed signature verifies over the canonical approval challenge.

After validation, mark/consume the decision durably before execution so replay cannot authorize a second execution.

### 4.4 Approval expiry

Default 300 seconds is acceptable initially, configurable within a bounded range.

On expiry:

- publish terminal rejected/expired result;
- archive the pending challenge as expired;
- do not execute;
- GUI removes it from pending view.

### 4.5 Multiple approvals

Multiple pending requests are allowed.

They are keyed by request ID and hash, never "approve next command."

Approving A must have no effect on B.

### 4.6 GUI behavior

The app must:

- be `LSUIElement`;
- expose a status item with count/badge;
- show a durable queue window;
- open queue window when app is explicitly launched;
- optionally bring a new item to attention once, but never rely on a modal `runModal`;
- not lose queue state when Mission Control/Spaces/window closure changes visibility;
- show full explanation first;
- show authority/effect summary;
- show cwd/repository;
- show exact command in selectable monospaced text;
- show short fingerprint + expiry;
- provide `Reject` and `Approve once`.

Security UX:

- **remove any default Return/Enter binding from Approve**;
- do not focus Approve by default;
- no "Approve all";
- no remembered blanket approval;
- no hidden command details.

For high-impact categories, `Approve once` performs a Secure Enclave/Keychain signing operation protected by user presence. A separate `LAContext` may be supplied to control the authentication interaction, but the durable authority is the verified signature over the exact challenge, not a boolean UI result.

### 4.7 Directory watching

Use a `DispatchSource.makeFileSystemObjectSource` watcher on pending directory.

On any event:

- rescan directory;
- validate all filenames/records;
- update in-memory view from disk truth.

Use a bounded fallback rescan timer only as recovery from missed/coalesced filesystem events, not as the primary mechanism.

---

## 5. Risk policy

Preserve existing least-privilege scope selection.

### No human approval by default

Only if current policy and scope allow:

- pure read-only repository work;
- ordinary repository-scoped Git/read/write where explicitly allowed by repository policy and no high-impact classifier matches.

### Human approval

Required for categories including at least:

- remote shell/copy (`ssh`, `scp`, `sftp`, `rsync`);
- non-repository filesystem mutation;
- system write;
- service-management/cutover;
- destructive operations;
- credential/permission changes;
- externally visible production deployment if classified high-impact.

### Step-up authentication

Require fresh device-owner authentication for:

- system write;
- remote shell/copy;
- destructive;
- credential/permission change;
- production deployment/cutover.

Do not weaken these categories merely to make the implementation autonomous.

---

## 6. Implementation phases

Each phase ends in a pushed durable branch checkpoint and an authoritative verification. Never combine two phases into one long bridge request.

### Phase A — reconcile and freeze base

1. Verify:
   - current branch;
   - local HEAD;
   - clean tracked/untracked state;
   - `origin/main`;
   - remote hardening branch.
2. Expected starting hardening SHA unless changed legitimately:
   `eb0b54b958422527196b33c49707230c151ded26`
3. Verify loaded labels and their program args/configs once.
4. Verify production and staging mailbox IDs match Section 1.
5. If base differs:
   - preserve work;
   - classify why;
   - rebase/merge only after explicit reconciliation.
6. Record exact new base in this file/project state if changed.

Success:
clean/reconciled branch + verified remote ref + service topology known.

### Phase B — finish approval state machine and GUI hardening

Implement on existing hardening branch.

Required source changes:

1. Non-blocking `AWAITING_APPROVAL` state.
2. `pending_approvals` health field.
3. versioned challenge/decision schemas.
4. consumed/replay-safe decisions.
5. symlink/path/ownership checks.
6. event-driven GUI directory watcher.
7. remove default Enter approval shortcut.
8. queue survives window close/Spaces/Mission Control.
9. Secure Enclave approver key provisioning + pinned public key.
10. signed `allow` decisions with user-presence access control.
11. exact authority/effect summary in challenge and UI.
12. deterministic schema/hash/signature golden vectors shared by Python tests and Swift self-test fixtures.
13. no fallback to JXA/`NSAlert.runModal`.
14. old modal helper may remain only as unreachable migration code until cleanup; production policy must not call it.

Targeted test request(s), each <120 seconds:

- Python approval protocol/state-machine tests.
- Swift typecheck.
- Swift self-test.
- installer portability tests.
- cutover static/unit tests.
- bridge legacy/v6 approval regression tests.

Checkpoint/push branch.
Verify remote hardening ref.

### Phase C — add macOS CI release gate

Add:

`.github/workflows/bridge-macos-ci.yml`

Required workflow properties:

- triggers: push to hardening/main and pull_request as appropriate;
- `permissions: contents: read`;
- stable macOS hosted runner (`macos-15` at plan time; verify availability at implementation);
- official actions pinned by full immutable SHA;
- `persist-credentials: false`;
- no secrets;
- Python matching supported runtime; production parity Python 3.14 is mandatory;
- `LLM_GIT_BRIDGE_TEST_JOBS=4`;
- run `bin/test`;
- compile/typecheck approval Swift code;
- run approval GUI self-test;
- `bash -n` installer/cutover.

If GUI compilation needs full Xcode, assert `xcodebuild -version` / SDK availability explicitly.

CI workflow itself must be tested by pushing the branch and observing the real GitHub check result through a supported authoritative surface. Do not infer success from YAML syntax.

If the current environment lacks a GitHub CI-result connector, use a public GitHub Actions page/API via web or GitHub's authenticated CLI on the Mac if already configured. Do not install credentials or tokens ad hoc.

Success:
complete suite passes on a clean macOS runner with normal system temp semantics.

### Phase D — repeat real disposable stage-only rehearsal

Use a fresh bridge request ID.

Operate from the exact hardening SHA that passed CI.

Redirect all installation/config/state/plist destinations into one uniquely named disposable directory inside the repository **only for the installer products**. The compiled candidate/self-test may use that tree, but do not run the Git-heavy full suite with `TMPDIR` there.

Verify:

- source SHA in manifest;
- bridge.py hash;
- workspace.py hash;
- approval helper hash if compatibility file remains;
- config hash;
- LaunchAgent plist hash;
- GUI Info.plist hash;
- GUI executable hash;
- app is ad-hoc signed;
- `LSUIElement`;
- approval root;
- allowed root;
- production/staging mailbox selection as expected;
- GUI `--self-test`;
- `--stage-only` does not load production v6 label.

Delete only the uniquely named rehearsal directory.

Success:
`STAGE_ONLY_ACCEPTANCE_OK=1` or equivalent verified postcondition.

### Phase E — fix/finalize transactional cutover

Existing audit defect still to close:

Successful production v6 smoke must be the irreversible cutover commit boundary.

Required ordering:

1. preflight all immutable staged hashes/config/source SHA; require `EXPECTED_SOURCE_COMMIT=<exact audited 40-hex SHA>` and reject any staged manifest whose `source_commit` differs;
2. verify cutover is controlled by a different bridge instance or true out-of-band process;
3. verify fresh production health;
4. verify production Drive request mailbox empty;
5. verify no pending approvals that would be lost;
6. verify `started_without_finished == 0`;
7. record rollback material for both loaded legacy production labels;
8. stop both:
   - `com.tom.chatgpt-shell-bridge`
   - `io.llm-git-bridge.daemon`
9. prove both are unloaded;
10. recheck mailbox after stop to close drain race;
11. start production:
    `net.laurenzo.local-executor-bridge`
12. doctor;
13. harmless production mailbox smoke;
14. verify result;
15. **set `CUTOVER_COMMITTED=1` immediately after successful production smoke**;
16. only then perform forward-repairable housekeeping:
    - compatibility `workspace.py` link;
    - archive old plists;
    - archive old runtime;
    - update status/docs.

If any pre-commit step fails:
rollback using captured old plist material and prove old production availability.

If any post-commit housekeeping fails:
**do not rollback healthy v6**. Mark housekeeping repair-pending and fix forward.

Add tests that assert this ordering structurally and behaviorally.

### Phase F — prepare cutover to be controllable by a different bridge instance

The existing v6 staging bridge uses a separate mailbox, but at the start of this plan it still runs the old modal approval code. Phase F changes cutover semantics and tests only. **Phase G must upgrade/qualify staging before it is used as the actual cutover controller.**

Improve `cutover.sh` from the current blanket "must not run through bridge" rule to:

- reject execution through the **same production mailbox/instance being replaced**;
- permit execution through a proven different control-plane bridge instance.

Propagate caller metadata into request child environment, at minimum:

- caller bridge instance ID;
- caller Drive root folder ID;
- caller state dir.

Cutover receives/reads target production root/instance and fails unless caller root differs.

Tests:

- same production bridge caller rejected;
- isolated staging caller accepted;
- missing caller identity rejected unless true Terminal/out-of-band mode is explicit;
- staging root equal target root rejected;
- no two consumers attach to production mailbox simultaneously.

This allows a fresh conversation to drive the final cutover through the existing staging bridge rather than asking the user to run Terminal commands.

### Phase G — bootstrap the isolated staging bridge with a locally authenticated Repair App

This phase exists because both currently installed daemons still use the broken JXA modal path. Do not skip it and do not assume the new approval GUI can attach to an old daemon.

Goal: establish one functioning isolated v6 staging controller **without asking Tomas to type Terminal commands and without disabling approval**.

#### G1 — qualify LaunchServices escape safely

1. Build a trivial signed repo-local test `.app` whose only effect is to write a uniquely named proof file inside a repo-local test directory and exit.
2. Launch it from the production v5 bridge with `/usr/bin/open <absolute-app-path>` using `write_scope: repository`.
3. Verify:
   - the request is not classified as high-impact;
   - `open` returns success;
   - the app process starts in the logged-in GUI session;
   - the proof file appears;
   - killing/ending the bridge request process group does not kill the launched app;
   - the test app performs no writes outside the repo-local proof directory.
4. Remove only the uniquely named test app/proof directory through an ordinary repository-scoped cleanup request.

If this qualification fails because current policy unexpectedly blocks LaunchServices, stop at a true user-only launch boundary. Do **not** disable confirmation or invent a broader system command.

#### G2 — build the Bridge Repair app

Add a small native Swift app to the repository, separate from the long-lived approval GUI.

The Repair app is a one-shot bootstrap controller, not another bridge daemon.

It must:

- contain or verify the exact expected candidate source SHA;
- display the exact staging-runtime action it will perform;
- display source and destination paths;
- display the staging mailbox identity/root ID;
- require fresh user presence using a Secure Enclave/Keychain-protected signing key or, for the bootstrap before that key exists, fresh device-owner authentication followed immediately by key provisioning;
- reject if any expected staging path/config/service identity differs from the audited plan;
- reject symlink/unexpected-owner destination paths;
- perform only the predefined staging-runtime transaction;
- write a local bootstrap audit receipt;
- have no generic shell/text-command input;
- have no "run arbitrary command" capability.

The app may use native process APIs to invoke the audited installer/staging logic with fixed arguments. Prefer calling a narrow repository-owned bootstrap subcommand rather than embedding duplicated installation logic in Swift.

#### G3 — staging runtime transaction

The Repair app must transactionally:

1. verify the candidate repo commit equals the expected audited SHA;
2. verify the v6 staging mailbox IDs are the isolated IDs in Section 1;
3. snapshot the current staging install/config/plist hashes and paths as rollback material;
4. stage the new candidate into a **new versioned staging runtime directory**, never overwrite the running old staging runtime in place;
5. verify install manifest + every generated artifact hash;
6. stop only `net.laurenzo.local-executor-bridge-v6-staging`;
7. atomically switch the staging plist/runtime pointer to the new version;
8. start staging;
9. verify fresh staging health;
10. verify staging root/request/result IDs still point only to the isolated staging mailbox;
11. run a harmless staging-mailbox smoke;
12. test one durable pending approval end-to-end using the new Mac approval GUI;
13. on any failure before smoke success, restore the captured previous staging plist/runtime and prove old staging health;
14. after smoke success, mark staging upgrade committed; old staging runtime becomes rollback archive only.

Invariant: **production v5 services remain untouched throughout Phase G**. The Repair app is permitted to mutate only the isolated staging runtime/config/plist and its own repo-local/bootstrap audit artefacts.

#### G4 — establish the durable approval client

Once staging is upgraded:

- launch the persistent native approval GUI;
- provision/pin the Secure Enclave approver public key;
- verify signed allow + reject + expiry + replay/tamper tests against **staging only**;
- verify approval wait does not occupy an execution worker;
- verify a safe staging request completes while another staging request waits for approval.

Success condition:

The isolated staging bridge, not production, is now the first deployed daemon using the durable signed approval protocol. From this point forward it can serve as the trusted control plane for production staging and cutover.

### Phase H — production stage and cutover

Only after B–G pass, including a successfully upgraded/qualified isolated staging bridge with durable signed approvals.

1. Stage exact CI/rehearsal-verified SHA into production locations.
2. Verify installed manifest and hashes again.
3. Keep existing v5 service(s) running until stage verification is complete.
4. Use isolated v6 staging bridge as cutover controller.
5. User approves the exact cutover challenge in the new GUI if policy requires.
6. Run transactional cutover.
7. Verify:
   - production v6 label loaded;
   - both duplicate v5 labels absent;
   - production mailbox has exactly one consumer;
   - staging bridge remains isolated;
   - current health is v6;
   - no STARTED-without-FINISHED;
   - harmless read request works;
   - repository-scope Git request works;
   - high-impact synthetic request reaches `AWAITING_APPROVAL` without blocking safe requests;
   - Reject produces no execution;
   - Approve once executes exactly once;
   - replay/tampering/expiry rejected.

### Phase I — browser/device-origin acceptance

Run the same harmless request pattern from:

1. ChatGPT in Mac Safari;
2. ChatGPT in Mac Firefox;
3. ChatGPT in iPhone Safari.

Expected:
origin does not change request/approval semantics.

For iPhone-originated high-impact request, Mac GUI approval is sufficient for v6 acceptance.

Record evidence/results in repo status.

### Phase J — retire staging and obsolete services

Only after production v6 acceptance passes.

Retire/disable/archive:

- `com.tom.chatgpt-shell-bridge`;
- `io.llm-git-bridge.daemon`;
- `net.laurenzo.local-executor-bridge-v6-staging`.

Do not touch:

`net.laurenzo.chatgpt-conversation-harness-v1`

Preserve staging mailbox evidence as archive; do not leave it as an active competing control plane after completion.

Verify loaded-service state independently.

### Phase K — ServiceManagement modernisation

After production stability, package registration according to current Apple guidance:

- UI as real app/login-item UI;
- daemon as embedded/registered agent where feasible;
- use `SMAppService` rather than long-term manual plist installation;
- preserve the single-daemon architecture;
- design migration transactionally and qualify in staging before production.

This phase may land as v6.1 if changing service registration during v6 recovery would materially increase cutover risk.

### Phase L — downloadable/open-source distribution hardening

Before calling the public package production-ready:

- Developer ID signing;
- Hardened Runtime;
- notarization with `notarytool`;
- stapling;
- Gatekeeper verification;
- release artifact checksums;
- documented installer/configuration;
- no Tomas-specific paths/default roots;
- no secrets;
- tests on a clean macOS account/machine if available.

This is not a prerequisite for restoring Tomas's own bridge.

---

## 7. Test matrix

Every item below needs a deterministic test or explicit acceptance evidence.

### Approval protocol

- exact request approved;
- reject;
- expiry;
- wrong nonce;
- wrong payload hash;
- wrong instance;
- modified command;
- modified cwd;
- modified write scope;
- decision replay;
- decision for request A cannot approve B;
- unsafe request ID;
- traversal ID;
- pending symlink;
- decision symlink;
- replaced pending file after UI read;
- daemon restart while awaiting;
- daemon restart after decision before STARTED;
- STARTED without FINISHED remains indeterminate;
- multiple concurrent approvals;
- approval wait does not consume execution slot;
- safe read request completes while another request awaits approval.

### GUI

- Swift typecheck;
- self-test;
- queue loads from disk;
- close/reopen window retains pending;
- app relaunch retains pending;
- menu/status count;
- DispatchSource update;
- fallback rescan;
- no default approval key;
- Reject immediate;
- Approve requires LocalAuthentication for configured categories;
- Secure Enclave key provisioning;
- public-key pin/fingerprint persistence;
- valid approval signature verifies;
- altered challenge/signature fails;
- user-presence cancellation fails closed;
- unavailable Secure Enclave/device auth handled safely;
- expired item cannot be approved;
- command text selectable and exact;
- no modification of command.

### Installer

- exact source provenance;
- dirty runtime source rejected;
- explicit SDK;
- generated artifacts hashed;
- app signed;
- stage-only starts no production service;
- non-Tomas default root behavior;
- mailbox adoption safety;
- same-mailbox upgrade requires cutover.

### Bootstrap / Repair app

- `/usr/bin/open` launch canary is not high-impact under current production classifier;
- launched GUI process survives completion of bridge request;
- Repair app has no arbitrary command input;
- expected candidate SHA mismatch rejected;
- staging mailbox/root mismatch rejected;
- unexpected/symlink staging destination rejected;
- staging old-runtime rollback material captured before stop;
- staging new-runtime health failure restores old runtime;
- staging smoke success commits upgrade;
- production v5 services untouched during staging bootstrap;
- Secure Enclave approver key provisioned only after explicit user presence;
- pinned approver public key survives staging daemon restart.

### Cutover

- staged source pin;
- all hashes verified before stop;
- duplicate v5 consumers both captured/stopped;
- partial stop rollback;
- missing rollback plist blocks before stop;
- fresh health required;
- mailbox empty before stop;
- pending approvals empty/handled;
- mailbox rechecked after stop;
- same-bridge controller rejected;
- isolated staging controller accepted;
- v6 start failure rollback;
- doctor failure rollback;
- smoke failure rollback;
- successful smoke commits transaction;
- housekeeping failure does not rollback healthy v6;
- old plists archived only after commit;
- only one production consumer after success.

### CI

- full `bin/test`;
- shell_bridge tests;
- Swift build/typecheck/self-test;
- shell syntax;
- no secrets;
- official pinned actions;
- macOS stable runner.

---

## 8. No-timeout orchestration recipe

A new conversation should work in **bounded slices**.

### Never do

- no polling loops;
- no repeated Drive listings waiting for one request;
- no multi-minute monolithic mutation+test+push+cutover command;
- no `bin/test` through production v5;
- no full-suite temp directories under repo;
- no speculative retries;
- no creating a second hardening branch because a result is slow;
- no `git pull` immediately before deployment;
- no unpinned installer execution.

### Standard mutation slice

1. one exact state read;
2. one code change bounded to one concern;
3. targeted tests under ~120 seconds;
4. commit;
5. push branch;
6. independent remote ref verification;
7. stop the slice.

### Standard async request recovery

If request result is not visible on first exact lookup:

1. inspect health once;
2. retain request ID;
3. do not resubmit;
4. next execution slice resumes from exact request ID.

### Long validation

Use CI, not a long bridge request.

### Production cutover

One isolated-staging-controller transaction with built-in rollback and bounded smoke attempts. Do not split "stop old" and "start new" across independent conversational requests.

---

## 9. CI implementation specification

Create a macOS workflow with roughly these logical steps; do not blindly copy this as final YAML without resolving immutable action SHAs.

1. checkout exact commit, no persisted credentials;
2. setup Python 3.14;
3. display Python/Xcode/Swift/Git versions;
4. set `LLM_GIT_BRIDGE_TEST_JOBS=4`;
5. `bin/test`;
6. Swift compile/typecheck of approval GUI against active macOS SDK;
7. compile temporary GUI binary and run `--self-test`;
8. `bash -n shell_bridge/install.sh shell_bridge/cutover.sh`.

Use `macos-15` unless official GitHub runner docs at implementation show it unavailable/deprecated.

Prefer one release-gate job first. Add architecture matrix only if a specific compatibility requirement justifies it; do not multiply CI complexity gratuitously.

---

## 10. Exact production acceptance definition

The bridge is not "done" until all are true:

1. GitHub canonical main contains the final audited implementation.
2. Remote `main` independently verified.
3. Production installed manifest points to that exact commit.
4. `net.laurenzo.local-executor-bridge` loaded.
5. `com.tom.chatgpt-shell-bridge` not loaded.
6. `io.llm-git-bridge.daemon` not loaded.
7. v6 staging retired after acceptance.
8. Conversation Harness untouched/running as independently intended.
9. Exactly one production mailbox consumer.
10. health fresh.
11. `started_without_finished == 0`.
12. no unexpected active request.
13. harmless read request succeeds.
14. repository mutation flow succeeds in isolated workspace.
15. high-impact request becomes durable pending approval.
16. unrelated safe request still executes while approval waits.
17. Reject prevents execution.
18. Approve once + user-presence-protected Secure Enclave signature executes exactly the bound request once.
19. replay/tamper/expired decision rejected.
20. GUI queue survives hide/window close/app relaunch.
21. request origin verified from Mac Safari.
22. request origin verified from Mac Firefox.
23. request origin verified from iPhone Safari.
24. docs/project-state reflects production reality.
25. obsolete live service plists/runtimes archived, not silently deleted before recovery value is gone.

---

## 11. Remote/iPhone-side approval — deliberately after operational recovery

Do not block v6 recovery on a public approval relay.

The durable approval schema above is intentionally transport-neutral.

When adding iPhone-side approval, use a real HTTPS relying party with passkeys/WebAuthn and user verification.

Requirements:

- server-generated cryptographically random challenge;
- challenge stored server-side until completion;
- exact transaction hash included/bound;
- short expiry;
- one-time consumption;
- WebAuthn RP/origin validation;
- user verification required;
- no LLM-generated `approved: true`;
- replay protection;
- Mac daemon verifies the resulting signed authorization artifact before execution.

References:

- Apple passkeys / public-private key authentication:
  https://developer.apple.com/documentation/authenticationservices/public-private-key-authentication
- W3C WebAuthn Level 3 challenge/replay requirements:
  https://www.w3.org/TR/webauthn-3/
- NIST SP 800-63B replay resistance:
  https://pages.nist.gov/800-63-4/sp800-63b.html

No public relying-party endpoint is currently established in this plan. A new conversation must not invent a domain, cloud account, secret, or relay service. Mac-side approval satisfies v6 operational acceptance for iPhone-originated conversations.

---

## 12. Immediate next actions for a fresh conversation

Do these in this order.

1. Read this file.
2. Reconcile exact branch/remote/service state.
3. Confirm hardening branch still contains `eb0b54b...` or identify legitimate successor.
4. Inspect current `approval_gui.swift`, approval state code, installer, cutover and relevant tests.
5. Implement Phase B as one or more small commits:
   - nonblocking durable approval state;
   - GUI watcher/UX hardening;
   - LocalAuthentication;
   - replay/symlink/expiry hardening.
6. Add and qualify macOS CI (Phase C).
7. Run disposable stage rehearsal (Phase D).
8. Finalize cutover transaction/controller semantics (E/F).
9. Qualify `/usr/bin/open`, build the one-shot Bridge Repair app, and transactionally upgrade only the isolated staging daemon (Phase G).
10. Prove staging signed approvals and concurrent safe-request behavior.
11. Integrate the fully verified branch to main.
12. Stage the exact final production commit.
13. Use the now-qualified staging bridge as production cutover controller.
14. Run production acceptance.
15. Retire staging/legacy services.
16. Update docs/status.
17. Only then resume dependent external work such as laurenzo.net staging deployment.

Do not ask Tomas to type `continue`. Continue across bounded phases automatically until either:
- production acceptance is complete; or
- the only remaining boundary is an explicit human approval/rejection that security policy intentionally requires.

---

## 13. User interaction policy

The implementation goal is **zero manual shell/admin work by Tomas**.

The security model still requires genuine human approval for high-impact execution. A new conversation must not bypass this requirement.

Acceptable user interaction:

- click/tap `Approve once` or `Reject`;
- Touch ID/device-owner authentication when required.

Not acceptable implementation dependencies:

- asking Tomas to paste Terminal commands;
- asking Tomas to edit plist/config files;
- asking Tomas to manually move keys/tokens;
- asking Tomas to restart daemons manually;
- repeated "continue" prompts.

---

## 14. External research references used for this plan

Apple:

- SMAppService:
  https://developer.apple.com/documentation/servicemanagement/smappservice
- NSStatusItem:
  https://developer.apple.com/documentation/appkit/nsstatusitem
- NSStatusBar:
  https://developer.apple.com/documentation/appkit/nsstatusbar
- LocalAuthentication / LAContext:
  https://developer.apple.com/documentation/localauthentication/lacontext
- Secure Enclave keys:
  https://developer.apple.com/documentation/security/protecting-keys-with-the-secure-enclave
- DispatchSource filesystem events:
  https://developer.apple.com/documentation/dispatch/dispatchsourcefilesystemobject
- Mach/XPC security guidance:
  https://developer.apple.com/documentation/xcode/conforming-to-mach-ipc-security-restrictions
- Hardened Runtime:
  https://developer.apple.com/documentation/security/hardened-runtime
- Notarization:
  https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution
- Passkeys:
  https://developer.apple.com/documentation/authenticationservices/public-private-key-authentication

Security:

- OWASP Transaction Authorization Cheat Sheet:
  https://cheatsheetseries.owasp.org/cheatsheets/Transaction_Authorization_Cheat_Sheet.html
- OWASP AI Agent Security Cheat Sheet:
  https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html
- NIST SP 800-63B:
  https://pages.nist.gov/800-63-4/sp800-63b.html
- W3C WebAuthn Level 3:
  https://www.w3.org/TR/webauthn-3/

CI:

- GitHub-hosted runners:
  https://docs.github.com/en/actions/reference/runners/github-hosted-runners
- actions/checkout:
  https://github.com/actions/checkout
- actions/setup-python:
  https://github.com/actions/setup-python

Comparable modern macOS patterns reviewed as secondary evidence, not authority:

- Lidless: menu app + SMAppService helper + XPC/watchdog
- vmenu: menu app + embedded SMAppService agent + XPC and explicit security model

Do not cargo-cult third-party code. Apple docs and this repository's threat model remain authoritative.

---

## 15. Continuation prompt for a fresh conversation

Use the following intent, but do not rely on this prompt instead of reading the file:

> We are finishing the unified Local Executor Bridge v6. Read `docs/unified-local-executor-v6-implementation-handoff-2026-10-03.md` in `/Users/tom/tom-repos/projects/llm-git-bridge` first, then use the persistent-adversarial-executor and mac-git-bridge workflow. Resume the existing `ai/installer-approval-integrity-20261003-a1` branch; do not redesign or create competing work unless authoritative state invalidates the handoff. Follow the phases and acceptance gates exactly, use bounded requests with unique IDs and non-empty explanations, no polling/loops, no replay of ambiguous mutations, use macOS CI for the complete suite, first bootstrap/qualify the isolated v6 staging bridge through the audited Repair App, then use it as cutover controller; never ask me to type shell commands, preserve genuine human approval for high-impact actions, and continue until production v6 acceptance is fully verified and legacy/staging bridge consumers are retired.
