# Local Executor Bridge v6

**Repository visibility policy:** agent requests may create repositories only with explicit private visibility. Creating a public repository or changing repository visibility to public is non-waivable and must be rejected before operator approval. Existing public repositories remain operable.

Local Executor Bridge v6 is the repository’s single production local executor. One daemon owns shell execution, the mailbox and durable request state, operator approvals, and the trusted Git workspace coordinator.

The older protocol-v2 Git implementation remains compatibility code; it is not a second production daemon. Git work is owned by this bridge through `workspace.py`.

## What it provides

- `bridge.py`: concurrent, supervised request execution with durable STARTED/FINISHED state.
- `workspace.py`: durable Git workspaces for substantial or concurrent repository changes.
- `install.sh`: macOS installer/updater that creates or adopts one verified Drive mailbox, pins its folder IDs, installs a LaunchAgent, runs `doctor`, and performs an end-to-end smoke test.
- `health.json`: a low-cost operational surface for diagnosing the bridge without submitting another shell request.

## Security boundary

**Filesystem and network least privilege by default.** Local Executor Bridge v6 treats raw shell execution as read-only unless the request is already inside an existing Git repository/worktree or is an exact invocation of the trusted workspace coordinator. On macOS, ordinary child shells run under `sandbox-exec`: read-only mode denies filesystem writes (apart from temporary runtime locations) **and network access**, while repository mode permits writes only to the current repository/worktree, its Git metadata, and temporary runtime locations **and still denies network access**. The exact bundled workspace coordinator is a separate trusted capability: it receives the repository/state authority and network access needed for fetch, checkpoint, push, and integration, while remotely supplied workspace `exec` and validation payloads are sandboxed again inside their worktree without network or ambient credentials. A request may explicitly ask for `"write_scope": "read_only"`, `"repository"`, or `"system"`; `auto` is the default. `system` write scope is never silent: it requires operator confirmation before `STARTED`.

**Menu-bar approval queue.** Reviews use nonmodal windows so the menu remains available. **Inspect exact command…** raises an existing review, and **Allow once** becomes available after explicitly choosing **Inspect exact command…** in this app session. **Review later** hides the review without deciding; expired or replaced requests cannot be approved from stale windows. v6 exposes a persistent bridge-icon status item. Its menu shows the running v6 source-build identifier, pending approvals, and an **About Local Executor Bridge** item; a numeric pending count appears beside the icon when work needs attention. The bridge uses an SF Symbols bridge image when available and falls back to `🌉`.

**High-impact operator confirmation.** On macOS, Local Executor Bridge pauses recognised high-impact control-plane/destructive commands *before* recording `STARTED` and hands the approval to a foreground LaunchServices helper, which presents a native confirmation dialogue to the logged-in operator. The request origin is deliberately irrelevant: a request submitted from ChatGPT in Mac Safari, Mac Firefox, or iPhone Safari follows the same Drive mailbox path and the same Mac-side approval broker; no browser extension, tab state, or browser-specific permission participates in approval. The dialogue leads with a plain-language explanation of the intended action and why approval is required, while the request ID, working directory, and actual command remain available in a wide selectable, scrollable details area; **Cancel** is the default. Declined or timed-out requests are terminally `rejected` and never reach the shell. Built-in recognition covers GitHub repository/content control-plane changes, mutating `gh api` requests, obvious mutating `curl`/`wget` forms, direct `ssh`/`scp`/`sftp`/`rsync`, force/delete pushes, `git reset --hard`, forced `git clean`, recursive forced `rm`, and common non-repository filesystem/system/package mutations. On non-macOS hosts, recognised commands are rejected by default. Set `operator_confirmation_mode` to `auto` (default), `dialog`, `reject`, or explicitly `off`; `off` weakens this safeguard. Command recognition remains defence in depth: ordinary read-only and repository-scoped raw shell execution has no network access; the trusted coordinator and explicitly approved `system` execution are the network-capable paths and remain subject to their own bounded authority and approval rules.

**The sandbox is scoped, not a VM.** Generic read-only requests deny both filesystem writes and network access. Repository-scoped raw shell requests also deny network access; Git fetch/checkpoint/push/integration networking belongs to the exact trusted workspace coordinator. Approved `system` requests run with the logged-in macOS user's normal authority. Treat the Drive mailbox as privileged infrastructure and do not put passwords, tokens, private keys, or other secrets directly in request command text, because request/result/journal bytes may persist.

For repository changes, use `workspace.py` rather than editing canonical branches directly. GitHub remains the canonical shared repository state; the Mac checkout is authoritative for current local execution state; Drive is transport only.

## Requirements

- macOS;
- Python 3;
- Git;
- `rclone` configured with a Google Drive remote;
- a local directory containing the repositories or files you intend to expose.

For sustained use, configure your own Google OAuth Desktop client for the rclone Drive remote rather than relying on rclone's shared client identity.

**October–November 2026 Google Workspace OAuth change:** the `is_agentic` classification applies to dedicated OAuth clients used by AI agents, including this bridge's automated transport. The 11 November restriction applies to Google's hosted **Workspace MCP** servers, not automatically to our current rclone/Drive REST mailbox. Before the setting becomes permanent on 12 November, inventory the actual active client ID, separate agent and non-agent clients, and avoid changing live credentials during an unresolved owner/journal handover. See [agentic OAuth decision and safe credential transition](../docs/GOOGLE-WORKSPACE-AGENTIC-OAUTH-2026.md).

## Fresh installation

Clone this repository and run the Local Executor Bridge installer:

```bash
git clone https://github.com/krahd/llm-git-bridge.git
cd llm-git-bridge
bash shell_bridge/install.sh
```

On a fresh host the installer will:

1. ask which local directory ChatGPT may use as the initial working-directory root;
2. choose an existing Local Executor Bridge mailbox when exactly one configured rclone remote already has one;
3. otherwise choose the only configured remote, or ask you to select a remote when several exist;
4. create `Local Executor Bridge/bridge-instance.json`, `requests/`, and `results/` when no mailbox exists yet;
5. resolve and pin the exact Drive folder IDs so later operation does not depend on ambiguous folder-name lookup;
6. install the bridge under `~/.local/share/local-executor-bridge` and its private config/state directories;
7. install and start the macOS LaunchAgent;
8. run `doctor` and an end-to-end smoke request.

The installer refuses to adopt an existing `Local Executor Bridge` folder that lacks a valid instance marker, and it refuses ambiguous duplicate live mailbox folders.

### Non-interactive installation

For automation, make the trust decisions explicit:

```bash
RCLONE_REMOTE=my-drive \
ALLOWED_ROOT="$HOME/repos" \
bash shell_bridge/install.sh
```

`RCLONE_REMOTE` accepts the remote name with or without the trailing `:`. `ALLOWED_ROOT` must already exist. `--stage-only` writes the installed files/config/LaunchAgent but does not explicitly bootstrap the agent or run the smoke request. **Do not stage a production-mailbox candidate plist directly under `~/Library/LaunchAgents`: macOS may auto-load it at a later login, even without a bootstrap command.** For replacement production staging use `--upgrade-stage-only` (or the guarded wrapper `stage_v6_upgrade.sh`), which requires an inert plist under its fresh installation directory and does not write to the live journal or mailbox. The one-invocation v5-to-v6 release controller now also uses that inert upgrade-staging mode, and only publishes the accepted v6 plist for future login *after* old v5 registrations are archived. A read-only smoke never proves real restart acceptance.

The installer also reserves a single operator-owned per-user installation lock
before remote mailbox discovery or local writes. Concurrent invocations cannot
silently race even if they choose different labels or directories. If an earlier
installer was forcibly killed, the reservation is intentionally left in place;
inspect partial installs and journal/LaunchAgent state before clearing it.
Never automatically steal a stale reservation.

**Do not rerun the installer over an existing live installation.** It
now rejects any pre-existing bridge install directory, config directory or
LaunchAgent plist before contacting Drive or writing local files. It also
rejects a direct install when loaded LaunchAgent ownership is ambiguous or
the target mailbox already has a loaded consumer, and rechecks just before
activation. An active v6-to-v6 change must be staged under fresh, isolated
paths with `--stage-only` and subsequently handed over by a separately
qualified, recoverable cutover procedure. The deprecated
`--retire-old-after-smoke` switch is now rejected outright; a fresh
install's smoke test can never retire another LaunchAgent. The original installation, journal
and rollback artifacts must remain intact. The v5-first-cutover controller
is **not** a v6-to-v6 upgrader and must not be run against a production
heartbeat already reporting v6.

### Read-only inventory before an existing-installation handover

On the Mac itself, run the source-verified local audit from the intended
repository revision:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 shell_bridge/audit_handover_readiness.py
```

The audit inspects loaded and parked launchd jobs, running PIDs, lock-file
descriptors, and the durable journals from **every distinct loaded bridge
state directory**, even when duplicate mailbox ownership makes the owner
inventory fail its safety gate. It neither starts/stops a service nor submits
requests. All output is limited to diagnostic metadata; it does not print
request command bodies or OAuth configuration. Inspect and preserve a `hold`
for each journal that cannot be verified, including historical staging states.
A missing or parked PID is not evidence that a LaunchAgent can never run again.

A passing local journal check does **not** prove an exclusive flock, an empty
Drive request queue, exact installed binary/build, native approval handling,
or recoverable rollback. The report therefore never authorizes activation or
legacy retirement. In particular, a staging mailbox that has already
double-executed requests must remain excluded from side-effecting operations
until an out-of-band, guarded ownership handover is verified.

If a journal reports `JOURNAL_HANDOVER_HOLD: journal contains indeterminate
completion`, **do not** delete the receipt, replay the request, stop running
services, or classify it as successfully completed. First run this bounded,
read-only local triage from an independently verified source checkout:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 shell_bridge/inspect_indeterminate_journal.py /Users/tom/.local/state/chatgpt-shell-bridge
```

It reports up to 40 affected request IDs and only controlled status-marker
labels, plus total counts from its bounded scan. It does **not** emit commands,
cwd, stdin, stdout, stderr or raw results; an incomplete scan explicitly
fails rather than declaring safety. Triage can locate receipts but cannot
prove whether an indeterminate command performed its external effects.
Reconciliation must inspect the exact request, affected repository/remote
state, and publication evidence separately under a controlled local process.
Even zero findings from triage do not authorize cutover.

### Upgrading or consolidating an existing installation

Use [V6_PRODUCTION_OPERATIONS.md](V6_PRODUCTION_OPERATIONS.md): stage an inert
release, review `v6_consolidate.py census`, then `v6_consolidate.py migrate`.
The sections below describe the superseded, label-per-candidate controllers
and are kept as history only.

### (Superseded) Guarded v6-to-v6 replacement: source pre-stop contract

For an existing v6 installation, use the separately controlled
`stage_v6_upgrade.sh` / `install.sh --upgrade-stage-only` workflow described
in [V6_UPGRADE_STAGING.md](V6_UPGRADE_STAGING.md). It prepares a fresh
candidate without changing the running state directory, remote mailbox, or
launchd job. Staging is **not** activation.

`cutover.sh` defaults to **v5-first** and must not be run directly against
an existing v6 production installation. The separate `upgrade_v6.sh`
entrypoint explicitly selects its guarded v6-to-v6 mode, using the tested,
pure `v6_to_v6_preflight.py` **pre-stop** decision gate. See
[V6_TO_V6_UPGRADE.md](V6_TO_V6_UPGRADE.md). Source and CI qualification of
that controller do not establish that the currently installed Mac services
can be safely switched.

It requires exactly one running, identifiable v6 production owner and no
conflicting loaded or parked claimant; matching source/candidate mailbox,
instance and durable journal; fresh PID-bound v6 health; no active work or
approvals; independently verified journal receipts; an empty remote request
mailbox; and a pinned, integrity-verified candidate. In particular, the
observed four running claimants and historic indeterminate production journal
**must fail** that gate. Never use a supplied JSON snapshot alone as authority:
a future executor must gather fresh, primary live state, then repeat mailbox
and journal checks after the old owner stops and before the replacement
starts.

A green **pre-stop** result is still not authority to mutate services. The
module never stops/starts processes, transfers ownership, replays ambiguous
requests, authorises rollback or retires v5. The separate controller has
additional post-stop, candidate-build, unique-owner and registration gates.
The historical duplicate production and staging consumers and indeterminate
journal on this Mac still block actual use; do not run the controller as a
service-repair command. A v5-first failure *before* candidate admission
likewise restores the old service only after a fresh journal, approval and
owner check. An unsafe rollback leaves a durable reconciliation HOLD.

## Request protocol

Create a raw JSON file named `<id>.json` in the mailbox `requests/` folder:

Prefer direct raw UTF-8 JSON upload. The runtime tolerates one optional UTF-8
byte-order mark (BOM) from a text export before parsing, but the exact
uploaded bytes, including that mark, remain part of the request SHA-256 and
replay identity. Other encodings, multiple BOMs, and non-object JSON requests
are rejected. Google Docs should not be used as a request-file transport;
its export format and metadata can introduce unrelated protocol errors.

```json
{
  "protocol": 1,
  "id": "repo-status-001",
  "cwd": "/Users/me/repos/example",
  "command": "git status --short --branch",
  "explanation": "Check the repository status before making changes.",
  "timeout_seconds": 30,
  "write_scope": "auto"
}
```

`write_scope` is optional. `auto` means repository-scoped **offline** writes when `cwd` is inside an existing Git repository/worktree, and filesystem-and-network read-only otherwise. Use `read_only` to force read-only execution, `repository` to require a Git repository/worktree scope, or `system` for an intentional write beyond repository scope; `system` triggers the native operator confirmation gate. The exact trusted workspace coordinator is recognised separately and receives only the network/repository/state authority required for its Git lifecycle. Obvious non-repository write commands detected under `auto` are also promoted to the confirmation gate instead of being run silently.

`explanation` is required on every request and must contain a concise plain-language description of the intended action. Missing, blank, non-string, or oversized explanations are rejected before execution. When local approval is required, the foreground macOS approval helper presents that explanation first and places the exact command, request ID, and working directory in an extra-wide selectable, scrollable details area.

IDs must be globally unique for the mailbox. The daemon publishes the terminal result under the same filename in `results/`.

Terminal statuses are:

- `completed`: the process reached a terminal state; inspect `exit_code`;
- `rejected`: the request did not run;
- `indeterminate`: STARTED was durable but FINISHED cannot be proved; inspect filesystem/Git state before retrying any mutation;
- timed-out/output-limited executions are reported explicitly and should be retried only at a smaller scope.

Do not busy-poll the mailbox. Submit once, retain the exact request ID, inspect that exact result, and use `health.json` once when the result is not yet visible.

## Repository work and recoverability

For anything substantial, create a durable workspace rather than mutating the canonical checkout from a conversational command:

```bash
python3 ~/.local/share/local-executor-bridge/workspace.py create \
  --repo /path/to/repo --resource docs/example --job-id unique-job-id --push-initial

python3 ~/.local/share/local-executor-bridge/workspace.py exec \
  --job unique-job-id --command '...' --checkpoint-message 'Describe checkpoint'

python3 ~/.local/share/local-executor-bridge/workspace.py ready --job unique-job-id
python3 ~/.local/share/local-executor-bridge/workspace.py integrate \
  --job unique-job-id --validate 'bin/test' --timeout 300
```

Each job owns a private branch/worktree and pushed checkpoints. Integration rechecks the latest target, overlap/conflict conditions, validation, push, and remote state before declaring success.

## ChatGPT-side use

The remote ChatGPT/LLM side needs permission to create raw JSON files in the chosen Drive `requests/` folder and read the matching raw JSON files from `results/`. The agent instructions should preserve these invariants:

- GitHub remote is canonical shared repository state;
- Mac checkout is authoritative local execution state;
- Drive is transport only;
- unique request IDs are never reused;
- an ambiguous delivery/tool timeout is not evidence that a Mac command failed;
- mutations are never replayed until actual Git/filesystem/remote state proves they did not complete;
- substantial Git changes use the durable workspace coordinator;
- completion requires commit, push, and independent remote verification unless the task is explicitly local-only.

A reusable agent-instruction template is provided in [`docs/shell-bridge-v5-client-guide.md`](../docs/shell-bridge-v5-client-guide.md).

## Diagnostics

The live Drive root contains `health.json`, which reports the bridge version, pinned Drive IDs, active requests, process ceiling, and STARTED-without-FINISHED state. A stale heartbeat is advisory rather than proof of daemon death: inspect the exact request result and local LaunchAgent/process state before restarting or resubmitting.

Local state lives under:

```text
~/.local/share/local-executor-bridge/     -> immutable release (symlink)
~/.config/local-executor-bridge-releases/<commit>/
~/.local/state/chatgpt-shell-bridge/      (production journal on migrated hosts)
~/.local/state/local-executor-bridge-locks/
```

See [`../docs/shell-bridge-v5-architecture.md`](../docs/shell-bridge-v5-architecture.md) and [`../docs/shell-bridge-v5-adversarial-audit.md`](../docs/shell-bridge-v5-adversarial-audit.md) for the design and threat-model details.


## Upgrade compatibility

Fresh v6 installs use `~/.local/share/local-executor-bridge` and `~/.local/state/local-executor-bridge`. During an in-place v5 production cutover, stage v6 with the existing production state directory so durable workspace jobs survive the service switch. After the v6 smoke succeeds, the cutover installs a compatibility link at the legacy coordinator path so older clients continue to reach the bundled v6 coordinator; the legacy path is not a second daemon.

### v6 approval UX and policy

v6 uses **effect-before-command approval**, not scope-before-command approval. Merely requesting broad `system` authority does not grant it and does not by itself create an approval prompt: safe requests are downgraded to repository or read-only authority. Approval is reserved for operations whose concrete effect is destructive/high-impact, escapes the repository boundary, or is not reasonably recoverable inside the normal Git workflow. The approval helper is a persistent menu-bar queue: popups are an attention mechanism, while pending approvals remain accessible from the menu bar until allowed, rejected, or expired. Approval is click-only; it must never require Touch ID, a password, LocalAuthentication, Secure Enclave user-presence, or equivalent identity authentication.

### Native approval-menu icon

The v6 approval menu bundles `assets/bridge-menubar.png` as an AppKit template image.
AppKit tints it automatically for light and dark menu bars; pending approval counts
appear as text beside the icon. The app does not use the previous emoji glyph.
