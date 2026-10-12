# Agent instructions

This repository implements `llm-git-bridge`, a provider-agnostic bridge between LLM/agent clients and local Git repositories.

## Live macOS safety hold (2026-10-12)

**Never submit side-effecting commands to the installed v6 staging mailbox**
`15ql2yACOq7H6qo0IgzosySqW8nHgUKXv`. The installed candidate at
`0b84b2f` demonstrably executed ONE read-only request twice concurrently
under distinct child PIDs (25404 and 25405), with different inherited
`TMPDIR` identities. This confirms a split-brain mailbox-owner failure,
not merely duplicate Drive result publication. The independent Drive
result IDs and exact request SHA are preserved in issue #39. Even a seemingly
idempotent Git/write action could run twice and corrupt or race the working
tree. **Do not use staging for repository edits, cutover, service changes,
approvals, or write-scope canaries.** Read-only investigations must preserve
unique request IDs and check every result file by immutable Drive ID.

The production mailbox `1PRsQHgVsgXhIYT_8akGFRGdhdlw9Lmk6` has a stale
heartbeat reporting older v6 and multiple installed LaunchAgents configured
for the same root. Do not assume that this is v5 or that stale heartbeat means
no daemon is loaded. Do not run `v6_release_once.sh` or `cutover.sh`
against an already-v6 production root, stop any legacy service by guessed
label/PID, replay ambiguous mutations, delete journals, or sunset v5 on source
qualification alone.

Canonical source PR #45 introduces stable cross-TMPDIR OS mailbox locking,
but source integration **does not update installed Mac binaries** or
retroactively prevent old daemons with different TMPDIRs from executing.
The next production change requires independent Mac-local loaded-owner and
journal verification, stopped-old-consumer proof, operator-authorized
handover, native approval test, mailbox canary uniqueness, and rollback
qualification. Keep issue #39 as the durable current acceptance gate.

When modifying the project:

- preserve provider-neutral terminology in core code and protocol;
- do not broaden raw shell authority beyond the Local Executor Bridge v6 resolved-scope and operator-approval model; repository/workspace execution is sandboxed but the bridge is not an OS security boundary;
- repository visibility is a non-waivable policy boundary: agents must never create public repositories or make repositories public; agent-created repositories must be explicitly private, and enforcement must reject rather than merely request approval;
- local filesystem paths must not appear in the remote repository index;
- retain stale-SHA checks and safe branch-prefix validation;
- minimise mailbox/Drive round trips without racing ambiguous Drive writes; Google Drive permits duplicate filenames;
- every ChatGPT Shell Bridge protocol-1 request must include a concise, non-empty, human-readable `explanation` describing what the command will do and why; this applies to read-only diagnostics as well as mutations;
- keep the standard-library-only runtime unless a dependency has a clear benefit;
- run `PYTHONPATH=src python3 -m unittest discover -s tests -v` before committing.
