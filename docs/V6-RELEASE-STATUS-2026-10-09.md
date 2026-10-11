# Local Executor Bridge v6 — release qualification and continuity

Updated 2026-10-09. Canonical design and release gates: `docs/plans/BRIDGE-V6-TRANSPARENT-EXECUTION-2026-10-09.md`. This is an execution checkpoint, **not** a declaration that production migration has passed.

## GitHub authority and completed work

- Canonical repository: `https://github.com/krahd/llm-git-bridge`, branch `main`; Mac repo `/Users/tom/tom-repos/projects/llm-git-bridge` may have unrelated dirty changes. Do not clean or reset it.
- Core v6 implementation workspace `v6-transparent-implementation-20261009-a3` was verified integrated at `275ccd65fa97f9ba3ed8b2d2ebdb28dee167fad8`; 126 tests passed, five skipped (Mac request `v6-preintegration-full-20261009-d15`).
- Runtime build identity workspace `v6-runtime-identity-20261009-a1` was integrated at `352a14df0748dcbfd2f54035fc98e5bf83aaefd9`; corrected whole suite ran 129 tests, five skipped (request `v6-resume-identity-tests-20261009-e19`).
- Native approval executable-manifest parity workspace `v6-build-identity-parity-20261009-a1` passed 22 focused tests, checkpoint `848b200a5bdad0f758a685a84ac7d216f3e9988f`, and integrated at `b7631beff8222e20cc9196ab72d5f247cb06899b` after coordinator full-suite validation (result `v6-identity-parity-integrate-20261009-f11`). This is the last *verified* v6 source release candidate for this checkpoint. If canonical main has advanced, reconcile against GitHub first.
- Installed-build `doctor` now compares manifest hashes for bridge, workspace, helper, and native approval app Info.plist/executable. This indicates artifact consistency, **not** approval authenticity, signed provenance, or production acceptance. Installer also records config and LaunchAgent hashes, which should be checked separately at cutover.

## Staging and production: distinguish source from runtime

- **Production:** v5 was still the live mailbox consumer when last verified. Production mailbox root `1PRsQHgVsgXhIYT_8akGFRGdhdlw9Lmk6` (requests `1jvTV0g4JVypFJ5olOovIPtCRIlawl4MP`; results `1SMdHzWEj2w2zRcOsCBc6fGT44ltco1fL`). No production cutover was authorized or executed in this work.
- **Staging:** v6 candidate `275ccd65fa97f9ba3ed8b2d2ebdb28dee167fad8` was installed and activated in the *separate* staging mailbox root `15ql2yACOq7H6qo0IgzosySqW8nHgUKXv`, requests `101ZPUf3ZLCG2BW8HLgQazg48TNyUMrNa`, results `1xnU55UNAc-6APc6b-8naPNyk4DlUC3xd` (requests `v6-resume-stage-build-20261009-e07` / `v6-resume-stage-activate-20261009-e08`). Last staged health observed 2026-10-09T21:03:20Z: bridge v6, zero active requests and zero unfinished durable STARTED requests.
- **Latest staging upgrade attempt:** request `v6-staging-upgrade-20261009-f13` reached a terminal **rejected** result at `2026-10-09T21:09:07Z`: `system_write` operator confirmation timed out, and the command **was not started**. No new staging build/worktree was created by this attempt, and production v5 was unchanged. The last staged build remains `275ccd65...`. Do not replay `-f13`; a new, explicitly human-approved upgrade must pin fresh canonical `main` and requalify that exact installed candidate.

## Staged end-to-end evidence on `275ccd65...` (not transferrable to a newer build)

- Read-only restricted Edu command completed exit 0 with read-only filesystem and network denied: `v6-stage-smoke-20261009-e09`.
- Logical-operation dedup across transport IDs `v6-stage-duplicate-20261009-e10`/`e11`: second result `replayed=true`; independent read `v6-resume-duplicate-verify-20261009-e13` confirmed *exactly one* appended line.
- Public GitHub repository creation was rejected *before shell execution*, with `policy_reason=public_repository_create`: `v6-stage-prohibited-20261009-e12`. This is not proof against every possible wrapped-shell bypass.
- Native GUI approval probe `v6-stage-approval-20261009-e14` was **rejected on confirmation timeout**, with `request was not started`. No human approval-path success is proven; do not mark GUI release gate passed.
- Attempt to freeze destructive GC in installed v5 coordinator `v6-resume-freeze-v5-gc-20261009-e15` was likewise **rejected on confirmation timeout, not started**. The installed production coordinator must be treated as capable of the old unsafe GC; **do not call installed v5 GC**.

## Mandatory stop/go gates still open

1. Once a real Mac human approval is available, create an exact-current-main staging build (the `-f13` attempt **never started**), verify manifest and executable identity, and rerun a small restricted Edu smoke. The former `275ccd65...` staging smoke does not certify the newer code.
2. In a genuine Mac GUI session, verify the native app displays the **full exact command before Allow**, correct category and path, clickable **Allow once/Reject** with no password/Touch ID, expiration and durable protected decisions. The `/usr/bin/open` success path alone does **not** establish actual visibility. A missed approval is fail-closed, not a passed UI test.
3. Authorize and verify installed-v5 GC freeze (including metadata and local-branch deletion), or implement an enforceable alternative. Source code fixes do not update installed v5.
4. Demonstrate real mailbox ownership exclusion across differently named instance IDs; delayed upload dedup; daemon restart/indeterminate handling; child-process containment; v6 mutation success followed by lost result; and safe v6-to-v5 rollback without replay. **Never resume v5 on an unreconciled v6 journal**.
5. Run at least two independent *new* restricted Edu conversations through the real connector to verify normal authorized read/write, GitHub completion, lost-result lookup and error classification. Carry forward time/output/concurrency limits and wake behavior.
6. Only then perform provisional v6 production cutover through the controlled, externally initiated migrator with preserved state and a tested hold/rollback path. Confirm exact installed commit and manifest, verified GitHub main and *one* production mailbox consumer before declaring final acceptance. Retire legacy services only afterward.

## Source remediation for missed approvals (not yet live-qualified)

- Diagnosis: the v6 native approval app presented approvals in a **menu only**. A successful `/usr/bin/open` merely launched a status-bar app, which does not prove the operator noticed the request. The terminal timeouts `-e14`, `-e15`, and `-f13` must remain **denied, not started**.
- Remediation source checkpoint `7fef7b46df9d65adccdc593dab62157e016eb7e6`, workspace `v6-approval-foreground-20261009-g1`, introduces a foreground native modal when a new approval payload appears. The modal includes the exact full command, directory, category, expiration, and untrusted agent explanation, plus **Reject / Review later / Allow once** choices. Reject is the default; deferral never grants permission. The UI rechecks the request ID, payload hash, and expiration before writing any decision, retains the menu as fallback, and returns to accessory mode when the dialog closes.
- Verification of this source patch: `v6-gui-verify-sdk-20261009-g08`, **22 focused tests passing** and **native Swift macOS SDK type-check passing**. This is not evidence that a dialog actually appeared on the operator's Mac or that native approval/rejection completed. No production v5 code or installation changed.
- **Live release gate remains open:** install the exact newest source revision into isolated v6 staging under an explicit authenticated Mac approval; demonstrate visible dialog, allow/reject/dismissal, and expired-request nonexecution. Preserve production v5 and do not cut over until all other stop/go gates also pass.

## Recovery instructions

Use the `persistent-adversarial-executor` and `mac-git-bridge` skills. GitHub `main` is canonical shared repository state; the Mac checkout and installed daemons are authoritative for local/runtime state; Google Drive is only transport. On resumption, inspect each named operation's **exact result** and the corresponding workspace/remote ref once before submitting any new mutation. Never retry a possibly executed operation under a new request ID without reconciliation. Preserve the dirty `ai/installer-approval-integrity-20261003-a1` checkout and existing worktrees. Do not invent acceptance receipts or grant system approval on behalf of the human.


## Update: installed candidate and protected approval, 2026-10-09

This section supersedes older staging-source and approval-timeout descriptions above; historical failed attempts remain preserved as evidence.

- GitHub canonical `main` was independently checked through the GitHub connector at `0b84b2f61be8aba4988d9c6b1181903f1febbbb3`. The foreground approval source change was integrated through workspace `v6-approval-foreground-20261009-g1` (integration result `v6-gui-targeted-integrate-20261009-g13`). Scoped v6 security/installer/cutover/UI tests passed; a broad suite under the nested macOS sandbox was not accepted as fully passing because sandbox-exec was denied by its enclosing sandbox.
- Isolated v6 staging was upgraded through a *real* operator-approved `system_write` request `v6-stage-corrected-20261009-g20`. The installed candidate is `candidate-0b84b2f-g2`, build source commit `0b84b2f61be8aba4988d9c6b1181903f1febbbb3`, `build_code_integrity=matches_manifest`, with independent staging root `15ql2yACOq7H6qo0IgzosySqW8nHgUKXv`. Previous failed request `v6-stage-upgrade-0b84-20261009-g17` did *not* install a candidate; it failed on duplicate staging-service label discovery, and the corrected invocation removed that override after reconciliation.
- Post-upgrade restricted-client smoke `v6-post-upgrade-smoke-20261009-g21` completed exit 0 with filesystem and network sandboxes enabled and confirmed exact source revision.
- The protected-command approval test `v6-gui-dryrun-force-push-20261009-g26` completed exit 0, with `operator_confirmation.required=true`, `approved=true`, and category `git_destructive_push`. It ran `git push --dry-run --force-with-lease . HEAD:refs/heads/ai/v6-gui-approval-probe-g26`: local `.` remote, dry-run only, no GitHub ref update. This is evidence that the new installed staging approval *decision path* works; it is not independent visual confirmation of the foreground dialog, Reject/Review-later behavior, or expiry handling.
- The lower-risk probes `v6-gui-allow-probe-20261009-g22` and `v6-gui-system-marker-20261009-g23` verified that requesting `system` is not sufficient to gain scope: the former ran at downgraded repository authority, and the latter was denied by filesystem sandbox outside the repository. This is expected least-privilege behavior.
- Production remains v5. **Do not cut over yet.** Remaining gates: observe and exercise foreground Reject/Review-later/Allow and expiration with Mac operator; qualify restart/indeterminate and lost-result recovery, mailbox exclusivity, child-process containment, rollback with journal reconciliation, independent restricted Edu clients, installed-v5 unsafe-GC freeze or enforceable alternative, and an operator-authorized production cutover with one consumer and rollback readiness. In particular, GitHub connector success does not grant access to Mac LaunchAgents or prove these runtime gates.
- Workspace `v6-stage-qualification-20261009-g2` was used for the staged smoke; preserve its state until the remaining qualification evidence is reconciled. No source edits are reported for that workspace here.


## CI qualification and connector-default decision (2026-10-09)

- **Default access choice:** When a GitHub connector is available, use it for canonical repository inspection, edits, commits and verification. Keep the local bridge as the independently useful shell/daemon fallback and for Mac runtime operations the connector cannot perform. GitHub is the authoritative repo state; Drive remains bridge transport only.
- GitHub Actions **Bridge v6 qualification** completed successfully for source integration `0b84b2f61be8aba4988d9c6b1181903f1febbbb3` (run `37995445160`) and for the current documented release checkpoint `6b729b1af4f75bf3087b19a184c77ab5b7174330` (run `37997646569`). These are CI results, not live Mac release certification.
- The isolated staging approval `v6-gui-dryrun-force-push-20261009-g26` was reconciled from its exact result: terminal completed, exit 0, `operator_confirmation.required=true`, `approved=true`, category `git_destructive_push`. The dry-run against local `.` created no actual ref; do not resubmit this probe. It validates live authenticated approval and command execution but not visual GUI presentation or rejection/defer controls.
- **Release status:** not production-accepted. v5 remains production until explicit rollback, exclusive-consumer, journal/late-result recovery, installed-GC safety, human reject/defer/expiry, and independent client acceptance evidence is recorded. Do not infer release acceptance from green CI or GitHub connector availability.
- **Read-only Mac verification currently submitted:** request `v6-installed-gc-audit-20261009-h01` in v5 production mailbox; check its exact result rather than replay. It inspects the installed v5 coordinator and staging service; no side effects are requested.


## Installed v5 GC emergency freeze — in-flight (2026-10-09)

- Read-only exact-result `v6-installed-gc-shape-20261009-h02` established the installed production coordinator file at `/Users/tom/.local/share/chatgpt-shell-bridge/workspace.py`, SHA-256 `f39ee60d70d78707e681281a6a6e5cad684267b7c05dd53cfa56597cc6def39`. Its `gc_jobs` function still attempts local worktree deletion, forced local branch deletion, optional remote branch deletion, and job-metadata unlink without checking all return codes. It is **not** the fail-closed source currently on GitHub main.
- Exactly one system-write request `v6-installed-v5-gc-freeze-20261009-h03` was uploaded to the v5 mailbox to insert an unconditional fail-closed exception at the `gc_jobs` entrypoint, using the exact SHA as compare-and-set, Python AST validation, an fsynced non-overwriting backup, and atomic replacement. **At this checkpoint the result is not yet known.** Inspect the exact `-h03` result in production results folder `1SMdHzWEj2w2zRcOsCBc6fGT44ltco1fL` and verify the installed file hash/behavior before marking the freeze complete. Do not upload a replacement, restart this action, or call v5 GC. A timed-out or rejected human approval means the freeze did not run.
- Staging remains on the isolated `0b84b2f...` v6 build; production remains v5. No production cutover was requested by this GC freeze.


## Installed v5 GC freeze verified (2026-10-09)

- Installed v5 coordinator `/Users/tom/.local/share/chatgpt-shell-bridge/workspace.py` was **successfully changed under real Mac `system_write` approval** by request `v6-installed-v5-gc-freeze-20261009-h05`. This inserted a fail-closed `WorkspaceError` at the start of `gc_jobs` before any deletion; the prior file was backed up to `workspace.py.pre-gc-freeze-20261009-f39ee60d70d7`. The installed SHA-256 after change is `62d64a910d25e839f0f8ff0e0e4631872fb7149b7220ddb9d64cbbc3bcf66dbb`.
- **Independent behavior verification** `v6-gc-freeze-verify-20261009-h08`: exact installed hash verified, guarded invocation of `workspace.py gc --repo /Users/tom/tom-repos/projects/llm-git-bridge` returned exit code 2 with `destructive GC frozen on installed v5 coordinator; use qualified maintenance build`, and the read-only verifier exited 0 with `INSTALLED_V5_GC_FREEZE_VERIFIED`. No workspace cleanup occurred. This closes the installed-v5 GC entrypoint gate for the verified file version, but not for other installed coordinator copies if discovered.
- The initial patch `-h03` and verifiers `-h06`/`-h07` refused correctly because a hash was mis-transcribed (the file had not changed). They performed no deletion or patch; `-h05` is the sole successful patch. Historical evidence remains in the v5 results folder. **Do not retry any of those request IDs.**
- Production is **still v5**; staging is **v6 `0b84b2f...`**. Do not infer migration acceptance from the verified GC freeze; the remaining live GUI controls, replay/restart, mailbox ownership, and rollback conditions still apply.


## Staging approval Reject probe: explicitly not passed (2026-10-09)

- Staging request `v6-gui-reject-probe-20261009-h09` ran the safe protected command `git push --dry-run --force-with-lease . HEAD:refs/heads/ai/v6-gui-reject-probe-h09`, with untrusted request explanation explicitly asking the operator to press **Reject**. The exact result showed `operator_confirmation.required=true`, `approved=true`, category `git_destructive_push`, and exit 0. The operation was a *local dry-run* and did not change GitHub or the local Git refs.
- This is **not** a rejection test pass: the trusted operator action was Allow. It confirms that Allow can execute a protected command; it provides no evidence that Reject works, that the operator saw the exact command, or that the UI is sufficiently comprehensible. Do not mark UI Reject/defer gate green, and do not extrapolate that the agent's descriptive text reliably governs a human decision.
- All other production handover gates remain as recorded. GitHub connector is now preferred for repository work; local staging/prod control remains separate and subject to verified operator approvals.


## Production candidate staged, rollback qualification pending (2026-10-09)

- Exact source SHA `0b84b2f61be8aba4988d9c6b1181903f1febbbb3` is the qualified v6 executable baseline; a GitHub comparison from it to the documentation commit `682aab89ef6eefdfa3e9d6a42e4d2235b88ce817` showed only this release-status document changed.
- With real local operator approval, request `v6-production-stage-only-20261009-h11` successfully staged a **non-running production v6 candidate** at `~/.local/share/local-executor-bridge`, config `~/.config/local-executor-bridge/config.json`, LaunchAgent `~/Library/LaunchAgents/net.laurenzo.local-executor-bridge.plist`. It pins existing production mailbox root `1PRsQHgVsgXhIYT_8akGFRGdhdlw9Lmk6` and the preserved v5 workspace state dir `~/.local/state/chatgpt-shell-bridge`. It did **not** activate the new service, stop v5, or change mailbox ownership.
- Read-only verification `v6-production-manifest-preflight-20261009-h13`: all **seven** manifest hashes match the candidate's bridge, workspace, helper, config, LaunchAgent, native approval app Info.plist and executable; source commit matches; legacy workspace exists; **production v6 is not loaded**. Production remains v5.
- The installer printed a warning about missing private OAuth, but read-only Mac request `v6-private-oauth-preflight-20261009-h12` established `private_client_id=True` in the redacted rclone config for both staging and production candidate. This is a diagnostic disagreement; do not expose, rotate, or replace OAuth credentials merely because of the installer warning.
- Before isolated rollback testing, the v6 staging health showed source `0b84b2f...`, no active request and zero unfinished STARTED operations, and the staging requests folder was empty.
- **In-flight system-write operation** `v6-staging-rollback-injection-20261009-h14`: bounded isolated staging test invokes `migrate_v6_staging_atomic.sh candidate-rollback-probe-h14` with `MIGRATOR_FAIL_AFTER_START=1`, then requires old staged LaunchAgent `net.laurenzo.local-executor-bridge-v6-candidate-0b84b2f-g2` loaded and test candidate unloaded. **Do not replay if no terminal result is visible.** Inspect the exact result in production v5 results folder `1SMdHzWEj2w2zRcOsCBc6fGT44ltco1fL` and reconcile staged LaunchAgents before proceeding.
- **Release remains unaccepted.** Provisional production cutover still requires independently established rollback after mutation/lost result, live approval Reject/defer, mailbox ownership exclusion, journal and queued-request reconciliation, independent restricted Edu clients or a documented connector-first replacement policy, and a quiescent production handover. The GitHub connector is preferred for repository work, not a substitute for local daemon cutover evidence.


## Staging rollback and durable replay qualification verified (2026-10-09)

- **Injected post-start staging rollback PASSED.** Read-only staging health before testing showed exact v6 source `0b84b2f61be8aba4988d9c6b1181903f1febbbb3`, no active requests and zero STARTED-without-FINISHED. Request `v6-staging-rollback-injection-20261009-h14` was operator-approved and executed the isolated staging migrator with `MIGRATOR_FAIL_AFTER_START=1`. Its terminal exit 0 reported `STAGING_ROLLBACK_AFTER_START_VERIFIED rc=1`, with old staging LaunchAgent `net.laurenzo.local-executor-bridge-v6-candidate-0b84b2f-g2` restored and injected replacement unloaded. **No production v5 service or mailbox was changed.** This verifies activation rollback after new staged service start, not recovery of an already executed v6 mutation with missing result.
- **Cross-restart logical-operation dedup PASSED.** Staging request `v6-restart-dedup-20261009-h15` with logical operation ID `v6-restart-idem-20261009-h15` wrote one line to unique test file in isolated staging worktree, terminal exit 0. Operator-approved request `v6-restart-staging-only-20261009-h16` restarted only the v6 staging LaunchAgent; refreshed staging health confirmed same source commit, different process PID, no active/unfinished operations. Subsequent request `v6-restart-dedup-20261009-h17` submitted identical normalized operation payload with a different transport request ID; terminal completed with `replayed=true` and `original_request_id=v6-restart-dedup-20261009-h15`. Independent read `v6-restart-dedup-count-20261009-h18` confirmed **exactly one** line; exact-byte-guarded cleanup `v6-restart-dedup-cleanup-20261009-h19` completed exit 0, printing `OWN_TEST_ARTIFACT_REMOVED`. No GitHub or local Git refs changed.
- These results close the *isolated staging post-start rollback* and *live cross-restart logical dedup* subgates. They do **not** close lost-result-after-mutation cross-version rollback, actual human GUI Reject/Review later, descendant containment, or production mailbox ownership/cutover acceptance. The production v6 candidate remains staged-only; v5 remains production.


## Second explicit Reject probe: allowed, not passed (2026-10-09)

- Staging `v6-gui-reject-probe-20261009-h20` was submitted after a user-visible instruction to select **Reject** and a matching untrusted agent explanation. The command was `git push --dry-run --force-with-lease . HEAD:refs/heads/ai/v6-gui-reject-probe-h20`, a local-dot dry-run that could not change GitHub or local refs. Its exact result was **completed, exit 0, operator_confirmation.required=true, approved=true, category=git_destructive_push**. Thus the operator's decision was **Allow again**, not Reject. No Git ref changed. This is a second unsuccessful live Reject qualification, after `v6-gui-reject-probe-20261009-h09` also returned Allow.
- **Do not treat agent prose as authority over a human decision, or infer a UI bug from an Allow result.** Nonetheless two unsuccessful attempts indicate approval-fatigue/UX acceptance is unresolved; do not claim live Reject or Review-later has been verified. Stop repeated prompts until a deliberate human rejection can be observed, or build a separately labeled UI test harness that does not forge a privileged approval.
- Core v6 source remains at `0b84b2f61be8aba4988d9c6b1181903f1febbbb3`; staged production candidate is present but **not loaded**. v5 production is active with destructive GC guarded. Isolated rollback-after-start and cross-restart at-most-once replay have passed, but **lost-result-after-mutation v6-to-v5 rollback**, live negative UI behavior, and production mailbox handover still have no acceptance receipt. No cutover or legacy retirement is authorized by these test results.


## Cutover fail-closed hardening integrated (2026-10-10 UTC)

- PR #1 (`ai/v6-cutover-preflight-failclosed-20261009`) passed GitHub Actions `Bridge v6 qualification` run `38017202975` at head `c203bde296d904e63c148c43bdbf69df0664c383`, and was squash-merged to canonical `main` at `b27c087ea4ab370f89f36ed0e9ed62df9980dcc3`.
- `shell_bridge/cutover.sh` now fails closed when either pre-stop or post-stop rclone mailbox listing fails rather than treating a failed listing as an empty queue. The cutover marks v6 as potentially started *before* invoking `launchctl bootstrap`, so an ambiguous bootstrap failure holds for journal reconciliation instead of blindly resuming v5. Focused regression assertions were added to `shell_bridge/tests/test_cutover.py`.
- The GitHub main file and integration commit were independently re-read after merge. **Source integration does not update the already staged Mac executable or prove live migration**; the staged candidate remains pinned to `0b84b2f...` until reinstalled and requalified against a fresh exact source commit.
- Remaining release blockers: deliberate observed human Reject/Review-later/expiry controls, lost-result-after-v6-mutation rollback reconciliation, descendant containment and mailbox exclusivity, up-to-date staged artifact/runtime verification and connector-first client acceptance. Do not cut over or retire v5 until these live gates have actual acceptance evidence. No new Mac mutation was issued by this source-only change.


## PR #2 cutover rollback guard qualified and integrated (2026-10-10 UTC)

- PR #2 head `bfc9ace394edd8b55d957ad18f24165b6b087996` passed GitHub Actions Bridge v6 qualification run `38019038446`; squash merge produced canonical `main` commit `6c8accac1eda773294a448fcd12709f55267836f`, independently verified via GitHub commit and `main` source readback.
- The cutover now arms legacy rollback responsibility **before** potentially ambiguous `launchctl bootout`; a failure return is no longer assumed to mean the old service remained loaded. Regression ordering assertion was updated.
- This is source-only safety hardening, not Mac staging qualification. Installed production v5 must remain running until a new exact-main candidate, rollback/journal tests, real negative GUI decisions, and exclusive-consumer handover pass. The existing staged candidate is older than canonical `main`.


## Additional rollback qualification (2026-10-10 UTC)

- PR #3 head `48607dd96d62818e806550f5908f4657607cbdb9` passed `Bridge v6 qualification` GitHub Actions run `38020571321`; squash-merged to canonical main at `bb0c159d1553aed263e3a17dec9451f4c0bead9a` (GitHub commit and source independently verified). Rollback from a pre-v6 cutover failure now checks each legacy LaunchAgent was restored, and if not, retains durable hold plus service recovery evidence.
- PR #4 `ai/v6-rollback-new-consumer-verification-20261009`, head `2a6e3271eb85f4a5be9979d69bffd68e2e594348`, adds explicit detection/reporting if v6 remains loaded after attempted rollback bootout, with an assertion that v5 is never restarted in that branch. Await CI qualification before integrating.
- None of these source changes has been installed as the staged production candidate. v5 remains authoritative production. Live staging approval negative controls, crash/lost-result cross-version handover, and Mac install identity are still unverified for latest source. Source-only progress is not a cutover authorization.


## PR #4 source integration and live mailbox inventory (2026-10-10 UTC)

- PR #4 head `2a6e3271eb85f4a5be9979d69bffd68e2e594348` passed Bridge v6 qualification run `38022223683`, then merged to canonical main at `ec600a2e38fb44f9b08ccbea1b2b04f14022c66a` (GitHub commit verified). The failed-v6-bootout recovery path now reports if v6 remains loaded and holds instead of resuming v5.
- Direct Google Drive health read of production root `1PRsQHgVsgXhIYT_8akGFRGdhdlw9Lmk6`: version 5, daemon PID 2079, zero active and unfinished requests, timestamp `2026-10-10T04:37:16Z`. Staging root `15ql2yACOq7H6qo0IgzosySqW8nHgUKXv`: version 6, PID 69679, source `0b84b2f61be8aba4988d9c6b1181903f1febbbb3`, manifest integrity matching, zero active and unfinished requests, timestamp `2026-10-10T04:36:48Z`.
- Thus the two verified daemons use separate mailboxes and state directories. The user's screenshot shows three bridge-style menu-bar icons, but does not prove a third daemon; an approval UI process may account for an icon. **Exact LaunchAgent/process inventory remains unverified** and no service should be stopped solely on icon count.
- The newest GitHub `main` has not been deployed to staging or production. Continue with a read-only local LaunchAgent/process inventory, then stage exact-main candidate and qualify negative GUI approvals and rollback without replay, preserving v5 production until cutover passes.


## Trusted-operation dispatcher source integration and Mac execution incident (2026-10-10 UTC)

- The deny-by-default helper contract was integrated at `710a9846bb3660b1ec65574b844edb8ac69544dc`. The typed trusted-operation dispatcher, CI regressions, installer/manifest parity and cutover validation were integrated via PR #7 at `92b8bf82f0db4234a852102ec81423f4ef6da500`. GitHub Actions macOS qualification run `38029569706` completed successfully (shell validation, Python regressions, native Swift typecheck). **The installed Mac staging candidate remains older `0b84b2f...`; source qualification is not live acceptance.**
- The Google Drive-to-Mac v5 request upload path was restored and proven working. Read-only inventory `v6-runtime-inventory-20261010-r03` completed but `ps` was denied by the filesystem sandbox. Even `write_scope=system` inventory `v6-launchagent-inventory-20261010-r05` was automatically downgraded to repository scope and denied process inspection, with no human approval. Thus the third menu-bar icon remains unidentified; two verified daemons use separate v5 production and v6 staging mailboxes.
- Local repository read `v6-reconcile-local-20261010-r04` showed the Mac checkout on dirty feature branch `ai/installer-approval-integrity-20261003-a1`, HEAD `10d36454...`, with uncommitted changes and many existing worktrees; **do not reset or clean**.
- Clean isolated workspace creation `v6-create-qualification-workspace-20261010-r06` became `indeterminate` (STARTED without FINISHED). Exact GitHub search and local coordinator read `v6-reconcile-job-20261010-r07`, plus local Git ref query `-r08`, found no job or matching branch/worktree. That attempted creation is reconciled as not applied.
- One fresh missing-delta attempt `v6-create-qualification-workspace-20261010-r09` returned **completed with exit code -15 (SIGTERM)** after 0.18 seconds; not a command timeout and not a daemon shutdown flag. Its execution plan reported a filesystem sandbox despite identifying the trusted coordinator. Exact GitHub branch/search, local coordinator read `v6-reconcile-workspace-b1-20261010-r10`, and local ref query `-r11` found no corresponding job or branch/worktree.
- Subsequent read-only local main-ref query `v6-read-local-main-20261010-r12` also became **indeterminate** (STARTED without FINISHED). No mutation is expected from its intended command, but the repeated journal interruption is a runtime-control-plane incident. Preserve the exact request IDs and result files. Do not blindly replay requests or submit further Mac mutations through the unstable consumer until its process/launchd and journal ownership are independently audited.
- **Production cutover is blocked.** Required next steps: restore stable and exclusively owned Mac executor control; identify loaded bridge/approval processes out-of-band with a trusted local operator; validate clean source staging at exact integrated SHA; test live negative approval, trusted helper operation, process containment, lost-result replay/recovery, and production mailbox ownership; only then perform provisional cutover, production smoke, acceptance and legacy retirement. No laurenzo.net deployment has been attempted by this work.

## One-invocation guarded Mac release implementation (2026-10-10)

- Source includes `shell_bridge/v6_release_once.sh`, a single local entrypoint
  that orchestrates full direct Mac qualification, dynamic ownership inventory,
  isolated staging, exact-SHA and read-only smoke, one deliberate native Deny
  and Approve, production-only staging against the original v5 mailbox and
  durable state, explicit critical MIGRATE authorization, provisional cutover,
  independent production approval, post-cutover exclusive mailbox audit, and
  archiving of stopped production v5 LaunchAgents for reboot safety.
- The controller never resets an existing checkout, assumes unconfirmed work
  finished, restarts v5 after v6 might have admitted work, adopts an existing
  candidate mailbox, or retires unrelated staging sessions based on labels.
  All ambiguous request/upload/results and release effects remain explicit
  reconciliation holds. A validated production handover preserves rollback and
  v5 journal evidence.
- Trusted read-only inspector installation is opt-in via the local installer;
  it is SHA-pinned outside the agent-writable repository root and every remote
  helper invocation independently requires native human confirmation.
- Added offline tests for real installer-embedded registration and release
  acceptance receipts, plus negative and positive native approval result
  verification and Mac launchd ownership collision detection. Mac CI runs both
  ordinary and full unsandboxed-*wrapper* / directly sandboxed regression suites.
- **Operational status: v5 production has not yet been replaced through this
  release controller.** Source and CI validation do not prove compatibility
  of this particular host, OAuth remote, native menu-bar UI, or third-party
  provider bindings. The local one-time operator run remains the release gate.
  Full live crash/replay fault injection is recorded as **not verified**; old
  staging instances stay preserved until their mailbox queues are reconciled.

## SSH and terminal readiness checkpoint (2026-10-10; GitHub main a833c2b)

- PR #13 integrated the no-prompt, operator-installed SSH read-only
  capability (ssh-pinned-readonly, actions status and identity) with
  host, user, port, identity, known-host and action restrictions. Install-time
  consent pins policy and known-host hashes; policy/key changes fail
  closed and require local operator re-provisioning. Raw arbitrary SSH/SCP/
  SFTP/rsync is rejected without a meaningless approval popup.
  Unrestricted Mac system shell remains unavailable to remote agents.
- PR #14 added interactive SSH enrollment within the same one-run release
  invocation if V6_SSH_POLICY_DIR is absent. It uses only existing trusted
  OpenSSH host keys, displays fingerprints, requires PIN SSH and refuses
  insecure SSH credentials and policy paths. Unknown keys are never
  automatically fetched or trusted.
- The one-run flow tests SSH status through isolated and provisional
  production v6. Before stopping v5 it expressly warns that only fixed
  SSH status/identity are qualified and requires the consequential response
  MIGRATE LIMITED SSH to accept limited SSH parity.
- Source qualification for PR #14: 219 tests passed in each of standard
  and direct macOS sandbox suites, shell syntax validated and Swift approval
  GUI typechecked. Canonical GitHub main independently matched
  a833c2b2e5f4e3719c15be368e18a233f8157c5e.
- Do not declare v5 sunset ready for general-purpose Mac administration.
  Source tests cannot verify this Mac's credentials, launchd services,
  Google Drive authorization, approval popups, exact mailbox ownership,
  live recovery semantics, or third-party provider behavior.
  Actual SSH intent needs clarification: SSH from the Mac to pinned hosts
  versus SSH into the Mac. The existing bridge transports commands to the
  Mac but policy restricts arbitrary system shell and outbound network.
- Production state: v5 remains production. No local cutover, v5 retirement,
  deletion, workspace reset, or website deployment occurred during this
  source-only remote pass. Preserve all staging instances and unresolved
  operations until locally reconciled.

## Fresh adversarial release audit (2026-10-10, pending qualified integration)

The previous single-run migration was **not actually release-ready** despite
passing its then-current suite. Source review found an invalid SSH status
canary ID (literal trailing dollar character) and a missing mandatory
operation_id in the native approval canary; both defects prevented the
corresponding live acceptance tests from reaching the intended operation.
Both are repaired with regression tests against the bridge's actual request
admission rules and the shell-expanded SSH canary ID.

The pinned outbound SSH helper now disables global OpenSSH known-host trust,
SSHFP host-key DNS verification and automatic host-key updates. Its executable
SHA-256 is part of the installed-build manifest, daemon integrity reporting,
staging prerequisites and production cutover manifest check. Python compilation
now runs explicitly before the costly full regression suite.

For **inbound SSH**, the Mac's Remote Login configuration remains an
operator-controlled OS service. The source adds a non-mutating access-group
and Remote Login preflight, with a read-only launchd fallback on macOS versions
where systemsetup requires administrator permission. A distinct SSH session
from a second device must supply a one-time nonce and non-loopback peer
metadata before the release can proceed to production. This is not a proof
of internet routing or a substitute for a genuine off-site connectivity test.
No code enables Remote Login, changes host access controls, modifies firewall
settings or retrieves untrusted keys.

Known limitations deliberately persist:
- Generic SSH/SCP/SFTP/rsync from the Mac are not authorized by the narrow
  pinned read-only status capability; general outbound command parity remains
  an operator-specific host/action design decision and must not be inferred.
- A nonlocal SSH session witness does not independently prove external
  internet or VPN reachability, which must be checked from the intended client.
- No live Mac cutover, native approval GUI, live recovery fault injection, or
  retired-staging inventory has been independently verified by remote CI.
- v5 remains the protected production consumer; this audit changed GitHub
  source only and did not touch the website or Mac runtime.

## Post-adversarial v6 stabilization checkpoint (2026-10-10; base 970c1ad)

Canonical GitHub source integrates the following independent hardening:

- PR #18: streaming limits on outgoing pinned SSH stdout/stderr; timeout or
  oversized output terminates the local SSH process group. Fixed-status SSH
  actions remain narrow and never auto-replay ambiguous remote effects.
  Qualified on macOS: 247 tests in each of two sandbox configurations.
- PR #19: high-impact approval classification for Git operations that discard
  worktree/index history (`restore`, forced checkout/switch, `git rm -f`,
  stash deletion and force branch deletion), while routine Git status/diff
  and index-only unstaging remain approval-free. Qualified on macOS:
  250 tests in each sandbox configuration.
- PR #20: an operator-only, single-invocation source launcher creates an
  isolated exact-`origin/main` release worktree; the local dirty/unpublished
  Mac checkout is not reset, cleaned, stashed, checked out, or overwritten.
  It refuses duplicate or ambiguous release sources. Qualified on macOS:
  254 tests in each sandbox configuration.
- PR #21: installation-time SSH consent also pins the selected private
  identity's SHA-256; rotating or replacing it invalidates approval-free
  status/identity operations. The private key is not committed or printed.
  Qualified on macOS: 250 tests in each sandbox configuration.
- PR #22: the isolated release launcher disables untracked local Git
  `post-checkout` hooks while preparing canonical source; regression tests
  create a deliberately side-effecting hook and assert it never runs.
  Qualified on macOS: 255 tests in each sandbox configuration.

A final **combined** CI run after these integrations is still required to
qualify their interaction; per-PR CI on divergent bases is not sufficient.

### Exact current release decision

**v5 remains protected production. No live cutover or v5 retirement has been
executed.** Local production-state truth requires inspection on the Mac.
Source tests cannot verify current LaunchAgents, native approval interaction,
Drive mailbox ownership, installed SSH credentials, private-network routing,
active requests, or replay/recovery behavior. Preserve unresolved operations
and historical staging services until independently reconciled.

The implemented SSH scope remains deliberately limited:
- **Inbound to the Mac:** operator-configured Remote Login for explicitly
  permitted accounts, plus a nonlocal-client acceptance witness. The witness
  uses client-provided `SSH_CONNECTION` metadata, which is not independent
  cryptographic attestation; the operator must personally perform the login
  from their intended remote device and verify connectivity and host identity.
- **Outbound from the Mac through v6:** operator-pinned, read-only SSH
  `status` and `identity` actions with no per-call prompt after enrollment.
  General SSH/SCP/SFTP/rsync and arbitrary remote administration are still
  not qualified, and raw system-shell escalation remains forbidden to agents.
- **Mac shell via v6 mailbox:** bounded sandboxed read/repository work is
  supported; potentially irreversible direct operations use native warnings
  when recognized. Static text analysis cannot prove arbitrary scripts safe.

The operator must decide whether limited outbound SSH functionality meets
their actual v5 parity requirements. The release script explicitly requires
`MIGRATE LIMITED SSH` before consequential production handover. Do not
enter that phrase if other SSH/terminal workflows remain required.

When the operator returns to the Mac, use the canonical documented one-line
bootstrap (not the old bare `v6_release_once.sh` from the dirty checkout).
Its first phase remains source checks and isolated staging. The real migration
must halt if SSH enrollment, native approval denial/allow tests, the external
client witness, production mailbox exclusivity, or live exact-build health
cannot be proven. No staging, website or existing dirty checkout should be
destroyed to make acceptance pass.

## Approval identity and final source qualification (2026-10-10)

PR #24, merged at `881f4c31ef6935de72b1f0efd1afa96fb9e402c9`,
resolved a further staging/production isolation risk: previously every
installed v6 native approval app had the same CFBundleIdentifier, although
its embedded approval directory differed. Each installed LaunchAgent now
generates a stable unique bundle identifier, and each approval GUI displays
its exact associated bridge-instance label in the popup and status tooltip.
The macOS qualification passed 257 tests in each standard and direct
sandbox suite; the Swift UI typecheck and installer shell syntax passed.
The tests execute the actual Info.plist generator with distinct stage/prod
labels, but **do not simulate macOS Launch Services or a human click**.

This checkpoint's subsequent macOS CI must run all integrated tests
**after** PR #24 merged, not merely the divergent individual PR branches.
This is source validation only. It is **not** authorization to activate
production v6 or retire v5.

Mac-local acceptance remains a genuine external prerequisite. The one
operator invocation described in V6-ONE-RUN-RELEASE.md starts from the
existing dirty checkout but builds from a private exact-main worktree.
The controller must prove staging execution, distinct native approval
DENY/ALLOW, the pinned outbound SSH read status check, an actual inbound
SSH session from the operator's second device, protected production mailbox
handover, live exact-build health, production native approval and exclusive
ownership. It must leave v5's journals, branches, workspaces and rollback
evidence intact. Do not retire v5 on CI results alone.

**General outbound SSH administration is not yet implemented.** The
release controller explicitly requires one-time `MIGRATE LIMITED SSH`
authorization before any v5 retirement; without a user-accepted restricted
scope and live Mac acceptance, production must remain v5. Unrestricted
agent system-shell execution also remains prohibited by design.


## Integrated v6 source release checkpoint — 2026-10-10

Canonical GitHub `main` was independently verified at
`6ed7cbb22adebe16bba57b14de3c6d4c8b5c88d2` after these changes:

- PR #26 (`5ae344b`) separated optional direct inbound macOS Remote Login
  acceptance from the Drive-backed Mac command transport. Opt-in
  `V6_REQUIRE_INBOUND_SSH=1` retains the restricted-account preflight and
  real second-device witness; omission does not enable Remote Login.
- PR #27 (`aac6c54`) rejects invalid inbound SSH opt-in values before staging.
- PR #28 (`dd95c4d`) supports operator-installed schema-2 SSH command profiles
  with fixed remote argv, pinned host keys and private identity, bounded
  output/time, no agent-supplied host/options/command, native per-call
  approval for custom actions, and an exact destination/command review.
  Built-in `status` and `identity` alone may use installation-time consent.
  Host or policy changes invalidate the capability.
- PR #29 (`6ed7cbb`) suppresses untracked local Git hooks both during initial
  `git fetch` and isolated worktree preparation, preserving dirty checkout
  state. A regression verifies a deliberately side-effecting
  `reference-transaction` hook normally runs but not during release fetch.

PR #28's macOS qualification run `38093475343` passed 265 standard tests
and 265 direct sandbox tests (six skipped in each), plus installer syntax,
Python compilation and Swift typecheck. PR #29's earlier-branch qualification
run `38093606964` passed 260 tests in each macOS suite (six skipped), plus
the same static/native checks. Because PR #29 was tested against a potentially
older merge base, these independent results are **not** sufficient to claim
the combined final tree was tested. A new integrated-main qualification of
this documentation checkpoint must pass before release source qualification
is considered complete.

**No Mac production migration or v5 sunset is recorded.** The current
source supports Mac-local sandboxed terminal work and explicit operator-pinned
outbound SSH profiles; it does not promise unrestricted arbitrary remote SSH,
SCP/SFTP/rsync, or unattended shell escalation. The production release still
requires exact host/operation-scope consent and actual local acceptance.
Known remaining external checks include native GUI Reject/Allow (and
expiration), SSH endpoint smoke and key-pin rejection, fresh mailbox/process
inventory, at-most-once journal reconciliation, actual production handover,
post-cutover approval, exclusive mailbox ownership, and a recoverable rollback
record. v5 GC protections and pre-existing local branches/state must remain
intact until these gates pass.

The operator's supported entrypoint remains the one-paste launcher documented
in `docs/V6-ONE-RUN-RELEASE.md`, using the latest qualified canonical main.
Do not instruct the operator to run earlier pinned-commit commands or perform
independent repeated migration scripts.
