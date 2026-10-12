#!/bin/bash
# Read-only, bounded Mac runtime inventory; never stop or modify a service.
set -euo pipefail
[ "$(uname -s)" = Darwin ] || { echo "macOS required" >&2; exit 2; }
UID_NOW="$(id -u)"
ROOT="$(cd "$(dirname "$0")" && pwd -P)"
echo "bridge_process_inventory_v2"
echo "legacy_launchagents:"
for label in com.tom.chatgpt-shell-bridge io.llm-git-bridge.daemon net.laurenzo.mac-executor-bridge net.laurenzo.local-executor-bridge; do
  if launchctl print "gui/${UID_NOW}/${label}" >/dev/null 2>&1; then
    printf 'loaded %s\n' "$label"
  else
    printf 'not_loaded %s\n' "$label"
  fi
done
echo "dynamic_bridge_launchagents:"
launchctl list | awk '/net[.]laurenzo[.]local-executor-bridge-v6|local-executor-bridge-v6-candidate/ {
  if (count++ < 24) print $1, $3
}'
echo "bridge_processes_no_arguments:"
# comm is the executable name or path, never its potentially sensitive argv.
# Continue consuming ps output after the cap; do not close a pipe early.
ps -axo pid=,ppid=,comm= | awk '
  /chatgpt-shell-bridge|local-executor-bridge|llm-git-bridge|local-executor-approval|Local Executor Approval|approval-helper|ChatGPT Shell Bridge/ {
    if (count++ < 40) print
  }'
echo "known_approval_apps:"
for app in "$HOME/.local/share/local-executor-bridge/Local Executor Approval.app" "$HOME/.local/share/chatgpt-shell-bridge/Local Executor Approval.app"; do
  if [ -d "$app" ]; then printf 'installed %s\n' "$app"; else printf 'absent %s\n' "$app"; fi
done
echo "verified_mailbox_owner_inventory:"
# This existing verifier redacts arbitrary argv and only prints identity fields.
PYTHONDONTWRITEBYTECODE=1 python3 "$ROOT/bridge_service_ownership.py"
echo "os_lock_file_descriptor_evidence:"
# Informational only: lsof establishes open descriptors, never ownership of
# an exclusive flock. A diagnostic failure is an explicit unresolved hold.
PYTHONDONTWRITEBYTECODE=1 python3 "$ROOT/bridge_lock_observer.py" ||
  echo "LOCK_DESCRIPTOR_EVIDENCE_INCONCLUSIVE=1"
echo "inventory_only_no_mutation=1"
