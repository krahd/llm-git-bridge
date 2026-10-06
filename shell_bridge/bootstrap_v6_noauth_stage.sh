#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SUFFIX="${1:-manual-bootstrap}"
case "$SUFFIX" in
  *[!A-Za-z0-9._-]*|'') echo "invalid suffix" >&2; exit 2 ;;
esac
LABEL="net.laurenzo.local-executor-bridge-v6-${SUFFIX}"
INSTALL_DIR="$HOME/.local/share/local-executor-bridge-v6-${SUFFIX}"
CONFIG_DIR="$HOME/.config/local-executor-bridge-v6-${SUFFIX}"
STATE_DIR="$HOME/.local/state/local-executor-bridge-v6-${SUFFIX}"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOCAL_EXECUTOR_LABEL="$LABEL" \
INSTALL_DIR="$INSTALL_DIR" \
CONFIG_DIR="$CONFIG_DIR" \
STATE_DIR="$STATE_DIR" \
PLIST="$PLIST" \
BASE_PATH="Local Executor Bridge v6 Staging" \
ALLOWED_ROOT="/Users/tom/tom-repos" \
"$ROOT/shell_bridge/install.sh" --stage-only
printf 'Staged v6 candidate only; not loaded: %s\n' "$INSTALL_DIR"
