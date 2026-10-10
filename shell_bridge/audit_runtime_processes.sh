#!/bin/bash
# Read-only identity inventory for the macOS bridge migration.
set -euo pipefail
[ "$(uname -s)" = Darwin ] || { echo "macOS required" >&2; exit 2; }
UID_NOW="$(id -u)"
echo "bridge_process_inventory_v1"
echo "launchagents:"
for label in com.tom.chatgpt-shell-bridge io.llm-git-bridge.daemon net.laurenzo.mac-executor-bridge net.laurenzo.local-executor-bridge; do
  if launchctl print "gui/${UID_NOW}/${label}" >/dev/null 2>&1; then
    echo "loaded ${label}"
  else
    echo "not_loaded ${label}"
  fi
done
echo "other_bridge_launchagents:"
launchctl list | awk '/net\\.laurenzo\\.local-executor-bridge-v6|local-executor-bridge-v6-candidate/ { if (count++ < 16) print $1, $3 }'
echo "bridge_processes:"
# Deliberately omit command arguments (which might include secrets).
ps -axo pid=,ppid=,comm= | awk '
  /chatgpt-shell-bridge|local-executor-bridge|llm-git-bridge|local-executor-approval|Local Executor Approval|approval-helper|ChatGPT Shell Bridge/ {
    if (count++ >= 32) exit
    print
  }'
echo "mac_approval_apps:"
for app in "$HOME/.local/share/local-executor-bridge/Local Executor Approval.app" "$HOME/.local/share/chatgpt-shell-bridge/Local Executor Approval.app"; do
  if [ -d "$app" ]; then echo "installed ${app}"; else echo "absent ${app}"; fi
done
echo "inventory_only_no_mutation=1"
