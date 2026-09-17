# Installation and setup

This guide sets up `llm-git-bridge` with the current Google Drive / `rclone` transport. The recommended path is deliberately interactive and re-runnable so that installation and later configuration use the same workflow.

## 1. Prerequisites

You need:

- Python 3.11 or newer;
- Git;
- `rclone`;
- a Google Drive account available through an `rclone` remote;
- one or more local directories beneath which you keep Git repositories.

The repository directories are **not** hardcoded by the project. Every host chooses its own repository roots during setup, and a host may configure zero, one, or many roots.

The reference production host currently uses Python 3.14, Apple Git 2.50, and rclone 1.75, but those exact versions are not required by the protocol.

## 2. Recommended: run the installer

Download the bootstrap script from the canonical repository and run it:

```bash
curl -fsSLO https://raw.githubusercontent.com/krahd/llm-git-bridge/main/install.sh
sh install.sh
```

The installer:

1. verifies Git, Python 3.11+, and `rclone`;
2. clones the canonical repository on first use, or fast-forwards an existing clean installation on later runs;
3. creates `~/.local/bin/llm-git-bridge` by default;
4. offers `rclone config` when no remote exists;
5. invokes `llm-git-bridge setup` for transport, repository roots, and root push policy;
6. on macOS, offers to install the bundled LaunchAgent.

It never contains or guesses repository roots. Those are chosen only in the setup wizard.

The installer is safe to run again. It refuses to overwrite local source modifications, uses fast-forward-only Git updates, and keeps bridge configuration outside the source checkout. Re-running it therefore acts as both an updater and a configuration entry point.

Useful installer overrides for advanced use:

```text
LLM_GIT_BRIDGE_REPO_URL
LLM_GIT_BRIDGE_REF
LLM_GIT_BRIDGE_INSTALL_DIR
LLM_GIT_BRIDGE_BIN_DIR
```

`--no-setup` and `--no-daemon` are available for scripted installation.

## 3. Manual installation

If you prefer to manage the source checkout yourself:

```bash
git clone https://github.com/krahd/llm-git-bridge.git
cd llm-git-bridge
bin/llm-git-bridge setup
```

The bundled wrapper runs the project directly from `src/`; package installation is optional. An editable package install remains available:

```bash
python3 -m pip install -e .
```

If you already know the name of an `rclone` remote, setup also accepts it explicitly:

```bash
bin/llm-git-bridge setup --remote YOUR_REMOTE_NAME
```

## 4. Configure one or more repository roots

Interactive `setup` asks repeatedly for directories containing Git repositories. A root is an ongoing discovery and policy boundary, not a one-time import list. For example, one person might keep repositories under `~/code` and `~/research`, while another might use a single `~/repos` root.

For each root, setup asks whether the LLM may push validated safe bridge branches for repositories beneath it. New repositories later created or cloned beneath that root inherit the same policy automatically.

You can inspect or modify roots non-interactively:

```bash
llm-git-bridge roots list
llm-git-bridge roots add ~/code --push enable
llm-git-bridge roots add ~/research --push disable
llm-git-bridge roots remove ~/research
llm-git-bridge roots scan
```

Nested roots with the same effective policy are redundant and are compacted. A nested root with a different policy is meaningful; the **most-specific containing root wins**.

The remote repository registry contains stable repository IDs and effective capabilities, never local filesystem paths. While the watcher runs, repositories created, cloned, removed, or moved beneath approved roots are reconciled automatically (30 seconds by default). `roots scan` is available for an immediate refresh. `configure-discovery --interval SECONDS` changes the bounded 5–3600 second discovery interval.

## 5. Configure validation commands

Remote requests cannot send arbitrary command lines. They may request only symbolic commands configured locally for a repository.

Example:

```bash
llm-git-bridge configure-command my-repo test \
  python3 -m unittest discover -s tests -v
```

A transaction can then contain:

```json
"run": ["test"]
```

Only configure commands for patched code you are willing to execute under your local user account. See [security.md](security.md#configured-command-trust-boundary).

## 6. Push policy and exceptions

Push has two independent gates:

1. local policy must permit push for the repository, normally inherited from its most-specific configured root;
2. the individual remote transaction must explicitly contain `"push": true`.

A repository can override its root policy when necessary:

```bash
llm-git-bridge configure-push my-repo enable
llm-git-bridge configure-push my-repo disable
llm-git-bridge configure-push my-repo inherit
```

`inherit` removes the repository exception and returns to root policy. Repository overrides are bound to the bridge's history-based repository identity, so replacing an unrelated repository at the same path does not inherit the old exception.

Even when push is permitted, the bridge can push only the transaction's validated safe-prefix branch to `origin`; it cannot force-push or remotely merge into a protected/default branch.

## 7. Reconfigure later

Run the same wizard at any time:

```bash
llm-git-bridge setup
```

Existing roots are shown first and retained by default. The wizard can add more roots, change their push policy, rescan repositories, and report Git/rclone/daemon readiness. A first persisted migration from the old version-1 configuration format preserves the original file once as the private `config.v1-backup.json` before writing version 2.

For automation that must not prompt:

```bash
llm-git-bridge setup --non-interactive --remote YOUR_REMOTE_NAME
```

## 8. Start the watcher

Foreground:

```bash
llm-git-bridge watch
```

One-shot processing is useful for development only when no persistent watcher is active:

```bash
llm-git-bridge watch --once
```

If the daemon is already running, `watch --once` correctly fails with `another bridge watcher is already running`; do not delete the lock file.

### macOS LaunchAgent

The installer offers this automatically. Manual installation is also available:

```bash
llm-git-bridge daemon install \
  --command "$(command -v llm-git-bridge)"
```

Management commands:

```bash
llm-git-bridge daemon start
llm-git-bridge daemon stop
llm-git-bridge daemon restart
llm-git-bridge daemon uninstall
```

The LaunchAgent writes logs under:

```text
~/.local/state/llm-git-bridge/daemon.out.log
~/.local/state/llm-git-bridge/daemon.err.log
```

On other operating systems, run `watch` under your preferred process supervisor. The repository currently provides first-class daemon management only for macOS.

## 9. Recommended: private Google OAuth client

For sustained use, configure a user-owned Google OAuth Desktop client instead of relying indefinitely on rclone's shared client identity. The repository includes a hardened migration helper:

```bash
python3 scripts/google_drive_oauth.py --help
```

Read [google-drive-oauth.md](google-drive-oauth.md) before running it. The browser authorisation step is necessarily interactive.

A remote `doctor` request reports whether a custom Drive OAuth client ID is configured.

## 10. Verify the installation

Check local state and roots:

```bash
llm-git-bridge status
llm-git-bridge roots list
```

Materialise a repository snapshot:

```bash
llm-git-bridge materialize my-repo
```

For remote verification, submit a protocol-v2 `doctor` request. A healthy rclone-RC setup should report a successful transport with `rc_enabled` and `rc_socket_healthy` true.

Continue with [usage.md](usage.md).
