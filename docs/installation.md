# Install Local Executor Bridge v6

This guide is for a **new installation** on an operator-controlled Mac. It does not migrate or replace a running v5/v6 bridge. The [production operations procedure](../shell_bridge/V6_PRODUCTION_OPERATIONS.md) is the single supported process for upgrades, consolidation and recovery. The older protocol-v2 guide is [archived](legacy-protocol-v2-installation.md).

## 1. Decide whether this is appropriate

Use the bridge only when an agent needs locally executed shell/Git work that a direct GitHub integration cannot perform, such as inspecting uncommitted code or running local tests. The bridge runs commands under your macOS account. A Drive mailbox with write access can submit commands to this trusted daemon; **do not share it with strangers or untrusted collaborators**.

Requirements:

- macOS with a logged-in operator for approval dialogs;
- Git and Python 3 available on a trusted system path;
- `rclone` with a Google Drive remote configured;
- an existing directory of Git repositories (the `ALLOWED_ROOT`);
- access to a client tool that can upload **raw JSON files** into Drive and retrieve their contents.

The bridge's installer rejects an interpreter or rclone binary resolved from inside the agent-writable allowed root. Install them outside that directory and verify the executables that will be used. Do not create an allowed root spanning your entire home directory merely for convenience.

## 2. Configure Drive

Install [rclone](https://rclone.org/install/) and follow its [Google Drive configuration](https://rclone.org/drive/) to run:

```bash
rclone config
rclone listremotes
```

Use a Google account under your control. A user-owned OAuth Desktop client is recommended for sustained use. A dedicated remote and mailbox are preferable to sharing one with other automation. The installer will choose a single configured remote or ask you to select one. Its default mailbox name is `Local Executor Bridge`.

Configure and verify the remote **before** installation. Do not commit `rclone.conf`, OAuth tokens, Drive credentials or a production mailbox marker into this repository.

## 3. Install from a complete checkout

```bash
git clone https://github.com/krahd/llm-git-bridge.git
cd llm-git-bridge
bash shell_bridge/install.sh
```

The compatibility entry point `sh install.sh` runs the same v6 installer from a full checkout. Do not download that wrapper by itself: it intentionally refuses an incomplete checkout rather than installing the obsolete protocol-v2 service.

The interactive installation asks for a local folder to expose, for example `$HOME/repos`. If you are setting up unattended *fresh* installation, provide `ALLOWED_ROOT` and `RCLONE_REMOTE` explicitly, but do not use unattended mode as an upgrade workaround.

The installer verifies its targets, refuses an existing installation/journal or conflicting active mailbox owner, creates one mailbox, pins Drive folder IDs, installs one macOS LaunchAgent and approval application, runs doctor, and attempts a harmless end-to-end smoke. A failed acceptance must be investigated; do not equate a successful file copy with a working bridge.

**Never run the installer, launchd operations or migration commands through the bridge's own mailbox.** Those actions require an interactive Terminal on the Mac.

## 4. Configure the agent / client

Authorize your chosen agent-side client to upload a **raw UTF-8 JSON file** to the Drive mailbox `requests/` folder and fetch the matching raw file from `results/`. Not every Google Drive integration offers raw-file creation. Connector text search, Google Docs creation and creating a file in the agent's temporary container do not, by themselves, establish that this upload path works.

A minimal read-only request:

```json
{
  "protocol": 1,
  "id": "example-status-2026-001",
  "cwd": "/Users/you/repos/example",
  "command": "git status --short --branch",
  "explanation": "Read the branch and working tree state of this repository.",
  "timeout_seconds": 30,
  "write_scope": "read_only"
}
```

Replace `cwd` with a real repository beneath the allowed root, and choose a unique request ID for **each new request**. Upload the bytes as `requests/example-status-2026-001.json` (not as a Google Doc or text pasted into a comment). Retrieve `results/example-status-2026-001.json` by exact name/ID; inspect `status` and `exit_code`. A `completed` envelope with nonzero exit code is not a passing command.

`health.json` in the mailbox root identifies the installed version and build, live folder IDs and active work. If an exact result is not immediately visible, check health once rather than constantly listing the results folder. A stale heartbeat alone does not prove daemon death. If delivery is ambiguous, **do not resubmit a mutation** until its journal and actual effects are reconciled.

For the currently tested Google Drive connector, a tool-produced file reference is accepted by `upload_file` as the **string** `file_uri.file_id`, not the surrounding `file_uri` object; verify the actual interface contract of your connector. The [2026-10-30 read-only smoke report](v6-client-smoke-2026-10-30.md) records this adapter-specific mismatch and measured result.\n\nA reusable [Mac Git Bridge skill](../skills/mac-git-bridge/SKILL.md) is supplied as a **template**. Replace the per-installation mailbox IDs and paths with your own, preferably in a private configuration. Never publish another user's mailbox IDs, credentials or personal repository paths.

## 5. Understand permission and approval behaviour

| Scope | Behaviour |
| --- | --- |
| `auto` (default) | Resolve the least authority available for the actual command |
| `read_only` | No persistent writes and no network |
| `repository` | Writes restricted to the selected existing repository/worktree; raw shell network disabled |
| trusted coordinator | Dedicated `workspace.py` can fetch/checkpoint/push for its governed Git lifecycle; remotely supplied commands are re-sandboxed |
| `system` | Logged-in-user execution after explicit local operator confirmation |

High-impact effects require an additional approval gate. Approval is effect-first, with exact-command inspection and explicit clicks in the Mac menu-bar queue. Do not confuse the scope requested by the agent with authority actually granted by the local daemon.

For substantial repository edits, use isolated durable workspaces with the installed coordinator. The coordinator must be the actual installed `~/.local/share/local-executor-bridge/workspace.py`, not a similarly named untrusted script. Do not attempt raw network Git push under `repository` scope.

## 6. Validate on the Mac

From the source checkout, run the repository's two suites:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
python3 -m unittest discover -s shell_bridge/tests
```

Then confirm: one daemon owns the mailbox; the `health.json` build manifest matches; a unique harmless read-only request completes exactly once; the Mac approval queue can allow, reject and expire a request; sandbox-denied writes/network are contained; timeouts preserve descendant containment; and a trusted workspace can create, checkpoint and integrate safely.

**The test suites do not replace these Mac acceptance checks.** Do not treat example output or a health heartbeat as proof that end-to-end client upload and readback work.

## 7. Existing installation, upgrade or recovery

Stop here if any prior v5/v6 service, LaunchAgent, journal or mailbox may exist. Do not run a new daemon against the same mailbox. Follow [V6_PRODUCTION_OPERATIONS.md](../shell_bridge/V6_PRODUCTION_OPERATIONS.md) for the interactive, inert-stage `v6_consolidate.py` process. It checks loaded labels, journal claims, locks, approvals, exact build and pinned mailbox identity before handover.

Do **not** delete journals or lock files, restart blindly, run a second watcher as a smoke test, replay an `indeterminate` result, or attempt production upgrades through the mailbox. Historical protocol-v2 commands and installer are not the v6 upgrade path.

For additional architecture and security details, see [Local Executor Bridge v6](local-executor-bridge-v6.md) and the [shell reference](../shell_bridge/README.md).
