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
