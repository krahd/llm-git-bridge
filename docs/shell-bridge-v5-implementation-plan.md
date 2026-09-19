# ChatGPT Shell Bridge v5 — Implementation Plan

Status: AUDITED / EXECUTION AUTHORISED
Date: 2026-09-18

## Definition of done

v5 is complete only when the converged architecture is checked into `krahd/llm-git-bridge`, all focused and full tests pass, the safe branch is pushed and remote-verified, production is deployed from that exact commit, live doctor/health and concurrent-workspace canaries pass, the Mac Git Bridge skill routes mutations through durable workspaces, Drive is tidied to one live mailbox plus a release archive, and canonical programme state says COMPLETE.

## Phase 1 — First-class source and transport hardening

1. Add `shell_bridge/` as the canonical source tree in the repository.
2. Import v4 behaviour without duplicate generated sources or pycache.
3. Add exact Drive-root-ID pinning and config migration support.
4. Add bounded rclone timeout and transport serialization.
5. Add `doctor` and heartbeat/health publication.
6. Add concurrent process-per-request admission with no fixed small worker pool, active-ID deduplication, graceful shutdown, durable active-process identity, and restart orphan containment.
7. Preserve all v4 replay/publication/limit semantics.

Verification: v4 regression parity plus new deterministic transport/concurrency/crash tests.

## Phase 2 — Durable workspace coordinator

1. Implement job create/list/show/recover/exec/ready/integrate/gc operations.
2. Create unique locked linked worktrees from exact fetched canonical refs.
3. Serialize commands within a job only.
4. On successful mutating exec, checkpoint with WIP commit, push private branch, and verify remote ref.
5. Make metadata atomic and reconstructable from Git evidence.
6. Implement resource key and path-overlap reconciliation gates.
7. Implement temporary integration worktree, squash candidate, validation, provenance trailers, pre-push revalidation, normal fast-forward push, and independent remote verification.
8. Preserve integrated/private branches for retention; fail closed on dirty/unverified GC.

Verification: disposable repositories with concurrent same-repo jobs, same-resource conflict, abandoned conversation recovery, remote target races, metadata-loss repair, and post-push/pre-metadata crash repair.

## Phase 3 — Installer, skill, docs and package

1. Make repository `shell_bridge/` the single release source.
2. Installer configures/preserves exact root ID and deploys bridge + workspace helper + LaunchAgent.
3. Package build excludes pycache/pyc and produces a manifest.
4. Update security/architecture/troubleshooting docs.
5. Update authoritative Mac Git Bridge skill: ordinary mutations use workspace jobs; raw shell remains read-only/maintenance/recovery default.
6. Doctor surfaces shared Google OAuth-client retirement warning without exposing secrets.

Verification: clean install/rerun simulation, package manifest verification, skill audit, no hard-coded personal repo roots in distributable defaults.

## Phase 4 — Qualification and adversarial repair loop

Run in bounded groups:
- validation/replay/transport tests;
- process concurrency/crash tests;
- workspace lifecycle/recovery/integration tests;
- installer/package tests;
- full repository `bin/test`.

Fault inject:
- daemon termination during execution;
- STARTED/FINISHED crash windows;
- rclone list/download/upload/delete timeout/failure;
- duplicate Drive names;
- result publication ambiguity;
- simultaneous jobs in one repository;
- same-resource and changed-path sibling work;
- GitHub target movement before push;
- metadata loss and worktree loss;
- GC against dirty/pending jobs.

Any material failure reopens the responsible architecture assumption and is fixed before proceeding.

## Phase 5 — Publish and deploy

1. Inspect final diff/status.
2. Commit the exact qualified candidate on `ai/shell-bridge-v5-20260918`.
3. Fetch/reconcile current `origin/main`.
4. Push safe branch and independently verify remote ref.
5. Integrate to main only after exact-base/revalidation and full qualification; no force push.
6. Verify remote main exact commit.
7. Stage/install production v5 from repository source without losing mailbox reachability.
8. Pin production config to live Drive root ID `1PRsQHgVsgXhIYT_8akGFRGdhdlw9Lmk6` and observed request/results IDs.
9. Restart daemon safely and verify doctor/health.

## Phase 6 — Live acceptance

Use disposable jobs/repositories only for destructive canaries.

Required canaries:
1. multiple independent shell requests overlap;
2. two different jobs in one repository overlap;
3. commands within one job serialize;
4. same-resource second integration refuses until reconciliation;
5. interrupted job remains discoverable/recoverable;
6. checkpoint private branch is visible on GitHub and remote-verified;
7. integration updates canonical target and `ls-remote` verifies exact commit;
8. publication failure simulation/legacy replay semantics remain safe;
9. heartbeat visible and doctor reports pinned IDs/rclone timeout/OAuth warning state.

## Phase 7 — Drive/state cleanup

1. Keep exactly one live folder named `ChatGPT Shell Bridge`.
2. Rename release archive clearly and retain only current v5 at top level.
3. Move v4 and older packages under deprecated after v5 acceptance.
4. Keep dated legacy-mailbox evidence archived; remove only empty/junk objects whose loss cannot impair recovery.
5. Ensure stale failed install/mailbox folders are not pollable and not name-colliding.
6. Update canonical STATUS/PLAN/WORKLOG/CONTINUATION and Mac Git Bridge skill.

## Completion audit

Fail completion if any of the following is true:
- current repo/source/runtime/package hashes disagree;
- full tests fail or were not rerun after final code changes;
- safe branch/main remote refs are not independently verified;
- production config is still name-only rather than root-ID pinned;
- live concurrency/recovery canaries have not passed;
- interrupted work can be silently lost or auto-integrated;
- same-resource sibling work can silently merge;
- v4 replay/publication guarantees regress;
- Drive still has ambiguous active mailbox naming;
- state documents do not match actual GitHub/Mac/runtime reality.

## Timeout/recovery discipline

Every source mutation, focused test group, full qualification, commit, push, deployment step and Drive cleanup operation is a separate bounded unit. After any ambiguous mutation, inspect actual repository/remote/runtime state before retrying. Never repeat a timed-out mutation at the same scope.
