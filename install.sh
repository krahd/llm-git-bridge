#!/bin/sh
# Compatibility entrypoint: v6 is implemented by shell_bridge/install.sh.
set -eu
SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
V6_INSTALLER="$SCRIPT_DIR/shell_bridge/install.sh"
if [ ! -f "$V6_INSTALLER" ]; then
  printf '%s\n' 'ERROR: Local Executor Bridge v6 requires a full repository checkout.' 'Clone https://github.com/krahd/llm-git-bridge.git and run bash shell_bridge/install.sh.' >&2
  exit 2
fi
exec /bin/bash "$V6_INSTALLER" "$@"
