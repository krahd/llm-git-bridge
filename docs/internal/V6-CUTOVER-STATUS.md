# v6 cutover status

- scope: complete v6 parity, approval-policy/menu-bar/no-auth migration, staging acceptance, regression, canonical integration, production cutover, rollback proof, legacy retirement
- canonical base at job creation: b0d08f77fd6e961e903f5ddce226dfab27ac0eb3
- durable job: bridge-v6-cutover-20261004-a1
- last verified checkpoint: 1633eaef5ea0e5be23d85e2721e1af352a40b63e (plan/status persisted)
- phase: Phase 2 — approval-policy/no-auth reconciliation
- in-flight ambiguous operation: none
- blocker: none; unpublished local main overlaps this surface but is protected and non-authoritative until published
- parity conclusion: v5 reliability/transport/recovery mechanics are already present on canonical main; port only effect-based queue/no-auth semantics plus any later authoritative correctness delta
- next bounded action: reconcile checkpoint 4015c39 and authentication-free invariant onto the cutover worktree while preserving canonical v5 compatibility fixes; run focused tests
