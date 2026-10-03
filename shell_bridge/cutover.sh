#!/bin/bash
set -euo pipefail

NEW_LABEL="${LOCAL_EXECUTOR_LABEL:-net.laurenzo.local-executor-bridge}"
NEW_PLIST="${NEW_PLIST:-$HOME/Library/LaunchAgents/$NEW_LABEL.plist}"
OLD_LABELS=("com.tom.chatgpt-shell-bridge" "io.llm-git-bridge.daemon" "net.laurenzo.mac-executor-bridge")
UID_NOW="$(id -u)"

[ "$(uname -s)" = Darwin ] || { echo "ERROR: macOS required" >&2; exit 1; }
[ -f "$NEW_PLIST" ] || { echo "ERROR: staged v6 plist missing: $NEW_PLIST" >&2; exit 1; }

# Caller must prove the production mailbox is quiescent immediately before cutover.
# This script performs only the bounded service switch and leaves old plists on disk for rollback.
for label in "${OLD_LABELS[@]}"; do
  launchctl bootout "gui/${UID_NOW}/${label}" >/dev/null 2>&1 || true
done
launchctl bootout "gui/${UID_NOW}/${NEW_LABEL}" >/dev/null 2>&1 || true
launchctl bootstrap "gui/${UID_NOW}" "$NEW_PLIST"
launchctl kickstart -k "gui/${UID_NOW}/${NEW_LABEL}"
launchctl print "gui/${UID_NOW}/${NEW_LABEL}" >/dev/null
printf 'LOCAL_EXECUTOR_BRIDGE_CUTOVER=1\n'
