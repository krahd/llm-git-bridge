# Public v6 client and documentation audit — 2026-10-30

## Scope and authority

Review the public-facing Local Executor Bridge v6 repository, its distributable `mac-git-bridge` skill, v6 agent request/result envelope and onboarding. GitHub `main` is canonical published source. The reference Mac's installed build and its local worktrees are a separate execution state. Google Drive is transport only.

Starting public `main`: `127c3b24b2e633ba50305f7f76038655494fcc43`. Reference host's previously observed installed v6 build: `a4286152f78615902a9b2a935d8bb423c940ddee`; manifest reported `matches_manifest`. **No deployment, daemon upgrade, approval or repository mutation was performed on the Mac for this documentation/client audit.**

## Confirmed defects and repairs

| Defect | Resolution |
| --- | --- |
| Contributor guide described retired protocol-v2 control plane as current | Replaced with v6-first contributor, test and production-safety instructions. |
| README and installation/security documents contained literal backslash-newline escapes | Restored paragraph breaks in public-facing entrypoints. |
| Main README and contributor guide sent v6 readers to the legacy security document | Linked current v6 architecture and client guide. |
| Protocol, usage, troubleshooting, architecture and concurrency guides could be mistaken for v6 production instructions | Labelled these as historical protocol-v2 references and pointed to v6. |
| Public skill metadata and bundled repo-layout reference identified a particular operator and personal Mac | Replaced with generic metadata and per-deployment discovery guidance; no live mailbox IDs included. |
| No portable way to verify a v6 request/result pair prior to or after transport | Added `skills/mac-git-bridge/scripts/v6_client_check.py`, with explicit limitations and usage docs. |

The top-level `install.sh` already delegated to v6 at the starting commit and was not changed. Historical protocol-v2 implementation and tests remain in the repository for compatibility; they should not be started alongside the v6 daemon.

## Verification performed

- Offline v6 client validator: 12 unit tests passed, including normal completion, rejected/indeterminate status, hash mismatch, invalid fields, duplicate JSON keys and output truncation.
- `v6_client_check.py health` passed against a captured reference-host v6 `health.json` without contacting or modifying the daemon.
- Python compilation of validator and tests passed.
- Skill structural validation and ZIP packaging passed. Packaged `SKILL.md`, `agents/openai.yaml`, `references/repo-layout.md` and `scripts/v6_client_check.py` had matching Git blob SHAs against the PR source.
- The previously recorded [2026-10-30 live read-only smoke](v6-client-smoke-2026-10-30.md) remains the live client-to-Mac execution evidence; this change did **not** repeat or extend that smoke.
- GitHub Actions macOS and Linux suites are required for PR acceptance; their status must be verified from the exact head commit before merge.

The offline checker checks request/result identity and exact raw-byte SHA-256. It does **not** authenticate the result author, decide safety, inspect remote execution, or verify Git push state. Successful output must not be described as end-to-end authorisation.

## Remaining acceptance and release boundaries

**Not established by this audit:** independent third-party first installation, native approval Allow/Reject/timeout behavior, sandbox deny/escape resistance on a deployed Mac, restart after crash/indeterminate recovery, production mailbox owner handover, and an end-to-end workspace checkpoint/integration/push under the current released build. Tests on isolated CI runners are not equivalent to deployed-host acceptance.

The public repository has **no explicit licence**; [issue #82](https://github.com/krahd/llm-git-bridge/issues/82) is a deliberate unresolved legal decision. Users should not infer redistribution permissions from public readability. [Issue #66](https://github.com/krahd/llm-git-bridge/issues/66) tracks Google Workspace agentic OAuth/client changes. Neither decision is implemented here.

## Operator-local validation sequence

On a test Mac **without an existing bridge**, use [installation](installation.md); on an existing Mac, use [canonical production operations](../shell_bridge/V6_PRODUCTION_OPERATIONS.md), never the bridge's own mailbox. Require the following evidence independently: one owner/one mailbox, installed-build/manifest identity, duplicate-ID replay behavior, read-only sandbox and raw-network denial, native approval Allow/Reject/expiration, descendant timeout containment, coordinator branch/worktree lifecycle, and verified remote integration. Treat ambiguity as a HOLD, not as permission to replay mutations.

Client-side procedure: [v6 client guide](v6-client-guide.md). Distributable skill: [Mac Git Bridge](../skills/mac-git-bridge/SKILL.md).
