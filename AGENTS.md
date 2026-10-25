# Agent instructions

This repository implements `llm-git-bridge`, a provider-agnostic bridge between LLM/agent clients and local Git repositories.

## Production bridge: exactly one v6 owner

The only supported production architecture is Local Executor Bridge v6 under
the single LaunchAgent `net.laurenzo.local-executor-bridge`. The canonical
install, upgrade, consolidation and recovery procedure is
[`shell_bridge/V6_PRODUCTION_OPERATIONS.md`](shell_bridge/V6_PRODUCTION_OPERATIONS.md)
using `shell_bridge/v6_consolidate.py`. v5 is retired, not a fallback.

- Never create an additional bridge LaunchAgent (no per-candidate,
  staging, verify or rollback labels) and never place a candidate plist in
  `~/Library/LaunchAgents`; stage releases inertly under
  `~/.local/share/local-executor-bridge-releases/<commit>/`.
- Never run service-management, install or migration commands through a
  bridge mailbox; they run in an operator Terminal on the Mac.
- Never weaken the mailbox lock union, the per-request claim, the
  no-replace STARTED marker or their regression tests
  (`shell_bridge/tests/test_mailbox_exclusivity_v6.py`): their absence let v5
  and v6, and two staging builds, execute the same requests (issue #39).
- Never delete or rewrite journals, approval records or ledgers, and never
  replay an `indeterminate` request automatically.

When modifying the project:

- preserve provider-neutral terminology in core code and protocol;
- do not broaden raw shell authority beyond the Local Executor Bridge v6 resolved-scope and operator-approval model; repository/workspace execution is sandboxed but the bridge is not an OS security boundary;
- repository visibility is a non-waivable policy boundary: agents must never create public repositories or make repositories public; agent-created repositories must be explicitly private, and enforcement must reject rather than merely request approval;
- local filesystem paths must not appear in the remote repository index;
- retain stale-SHA checks and safe branch-prefix validation;
- minimise mailbox/Drive round trips without racing ambiguous Drive writes; Google Drive permits duplicate filenames;
- every ChatGPT Shell Bridge protocol-1 request must include a concise, non-empty, human-readable `explanation` describing what the command will do and why; this applies to read-only diagnostics as well as mutations;
- keep the standard-library-only runtime unless a dependency has a clear benefit;
- run `PYTHONPATH=src python3 -m unittest discover -s tests -v` and `python3 -m unittest discover -s shell_bridge/tests` before committing.
