# Installation and setup

This guide sets up `llm-git-bridge` with the current Google Drive / `rclone` transport.

## 1. Prerequisites

You need:

- Python 3.11 or newer;
- Git;
- `rclone`;
- a Google Drive account available through an `rclone` remote;
- at least one local Git repository you want the bridge to expose.

The reference production host currently uses Python 3.14, Apple Git 2.50, and rclone 1.75, but those exact versions are not required by the protocol.

## 2. Clone the bridge

```bash
git clone https://github.com/krahd/llm-git-bridge.git
cd llm-git-bridge
```

The `bin/llm-git-bridge` wrapper runs the project directly from `src/`; no package installation is required for normal use.

Optional editable installation:

```bash
python3 -m pip install -e .
```

## 3. Configure rclone

Create or identify an `rclone` Google Drive remote, then point the bridge at it:

```bash
bin/llm-git-bridge setup --remote YOUR_REMOTE_NAME
```

The bridge creates and uses its own mailbox tree under that remote.

### Recommended: private Google OAuth client

For sustained use, configure a user-owned Google OAuth Desktop client instead of relying indefinitely on rclone's shared client identity. The repository includes a hardened migration helper:

```bash
python3 scripts/google_drive_oauth.py --help
```

Read [google-drive-oauth.md](google-drive-oauth.md) before running it. The browser authorisation step is necessarily interactive.

You can check the current transport state with a remote `doctor` request. `custom_drive_client_id_configured: false` means the private-client migration has not yet been completed.

## 4. Approve repository roots

Add one or more directories under which the bridge should discover Git repositories:

```bash
bin/llm-git-bridge add-root ~/repos
bin/llm-git-bridge status
```

The remote repository registry contains repository IDs and state, not local filesystem paths. The watcher automatically discovers new Git repositories created or cloned beneath approved roots (30 seconds by default), so normal operation does not require another `scan`. Use `scan` only when you want an immediate full state refresh, and use `configure-discovery --interval SECONDS` to change the bounded 5–3600 second discovery interval. Adding a new root remains an explicit local action.

## 5. Configure validation commands

Remote requests cannot send arbitrary command lines. They may only request symbolic commands configured locally for a repository.

Example:

```bash
bin/llm-git-bridge configure-command my-repo test \
  python3 -m unittest discover -s tests -v
```

A transaction can then contain:

```json
"run": ["test"]
```

Only configure commands for patched code you are willing to execute under your local user account. See [security.md](security.md#configured-command-trust-boundary).

## 6. Decide whether pushes are allowed

Commits are local by default. Remote-triggered push requires both local opt-in and an explicit transaction flag.

Enable:

```bash
bin/llm-git-bridge configure-push my-repo enable
```

Disable:

```bash
bin/llm-git-bridge configure-push my-repo disable
```

When enabled, the bridge can push only the transaction's validated safe-prefix branch to the hard-coded `origin`, never force-push or merge.

## 7. Start the watcher

Foreground:

```bash
bin/llm-git-bridge watch
```

One-shot processing is useful for development only when no persistent watcher is active:

```bash
bin/llm-git-bridge watch --once
```

If the daemon is already running, `watch --once` correctly fails with `another bridge watcher is already running`; do not delete the lock file.

### macOS LaunchAgent

Install once:

```bash
bin/llm-git-bridge daemon install \
  --command "$PWD/bin/llm-git-bridge"
```

Management commands:

```bash
bin/llm-git-bridge daemon start
bin/llm-git-bridge daemon stop
bin/llm-git-bridge daemon restart
bin/llm-git-bridge daemon uninstall
```

The LaunchAgent writes logs under:

```text
~/.local/state/llm-git-bridge/daemon.out.log
~/.local/state/llm-git-bridge/daemon.err.log
```

On other operating systems, run `watch` under your preferred process supervisor. The repository currently provides first-class daemon management only for macOS.

## 8. Verify the installation

Check local state:

```bash
bin/llm-git-bridge status
```

Materialise a repository snapshot:

```bash
bin/llm-git-bridge materialize my-repo
```

For remote verification, submit a protocol-v2 `doctor` request. A healthy rclone-RC setup should report:

```json
{
  "status": "success",
  "doctor": {
    "transport_type": "rclone",
    "rc_enabled": true,
    "rc_socket_healthy": true
  }
}
```

Continue with [usage.md](usage.md).
