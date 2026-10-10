#!/bin/bash
# One-command, fail-closed local v6 qualification and isolated staging.
# Never stops a loaded service, moves a mailbox, or retires legacy v5.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$ROOT"
[ "$(uname -s)" = Darwin ] || { echo "macOS required" >&2; exit 2; }
for tool in git python3 rclone launchctl swiftc; do
  command -v "$tool" >/dev/null || { echo "missing dependency: $tool" >&2; exit 2; }
done
if [ "$#" -ne 0 ]; then
  echo "usage: bash shell_bridge/prepare_v6_on_mac.sh" >&2; exit 2
fi
printf '\n=== Bridge v6: immutable source snapshot ===\n'
git rev-parse --show-toplevel
git rev-parse HEAD
if [ -n "$(git status --porcelain)" ]; then
  echo "STOP: this checkout has local changes. Run from a clean dedicated worktree; nothing was modified." >&2
  exit 3
fi
printf '\n=== Local regression tests ===\n'
for script in shell_bridge/install.sh shell_bridge/cutover.sh shell_bridge/migrate_v6_staging_atomic.sh shell_bridge/prepare_v6_on_mac.sh; do
  bash -n "$script"
done
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s shell_bridge/tests -p 'test_*.py'
printf '\n=== Read-only mailbox inventory ===\n'
report="$(mktemp)"
trap 'rm -f "$report"' EXIT
PYTHONDONTWRITEBYTECODE=1 python3 shell_bridge/bridge_mailbox_inspector.py > "$report"
python3 - "$report" <<'PY'
import json, sys
data=json.load(open(sys.argv[1],encoding='utf-8'))
for record in data['records']:
    print(record['label'],record.get('service_state'),record.get('config_status'),
          record.get('drive_root_folder_id','unknown'))
duplicates=data.get('shared_drive_roots',{})
if duplicates:
    print('HOLD: shared mailbox roots:',json.dumps(duplicates,sort_keys=True))
    raise SystemExit(5)
if any(record.get('config_status')!='readable' for record in data['records']):
    raise SystemExit('HOLD: mailbox identities are incomplete; preserve all running services')
print('MAILBOX_OWNERSHIP_PREFLIGHT=PASS')
PY
printf '\n=== Stage new candidate; never use production mailbox ===\n'
source_commit="$(git rev-parse HEAD)"
suffix="candidate-${source_commit:0:12}"
label="net.laurenzo.local-executor-bridge-v6-${suffix}"
config="$HOME/.config/local-executor-bridge-v6-${suffix}/config.json"
plist="$HOME/Library/LaunchAgents/${label}.plist"
install="$HOME/.local/share/local-executor-bridge-v6-${suffix}"
state="$HOME/.local/state/local-executor-bridge-v6-${suffix}"
if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
  echo "HOLD: candidate label already loaded; will not overwrite running service" >&2; exit 6
fi
if [ -e "$config" ] || [ -e "$plist" ] || [ -e "$install" ] || [ -e "$state" ]; then
  echo "HOLD: candidate installation already exists. Reconcile before reuse; nothing overwritten." >&2; exit 6
fi
if [ -z "${RCLONE_REMOTE:-}" ]; then
  RCLONE_REMOTE="$(python3 - "$HOME/.config/chatgpt-shell-bridge/config.json" <<'PYREMOTE'
import json,sys
try:
    value=json.load(open(sys.argv[1],encoding='utf-8')).get('remote')
except (OSError, ValueError):
    raise SystemExit('HOLD: legacy bridge config unavailable; specify RCLONE_REMOTE explicitly')
if not isinstance(value,str) or not value.endswith(':'):
    raise SystemExit('HOLD: legacy remote invalid; specify RCLONE_REMOTE explicitly')
print(value)
PYREMOTE
)"
fi
export RCLONE_REMOTE
ALLOWED_ROOT="${ALLOWED_ROOT:-$HOME/tom-repos}" \
BASE_PATH="Local Executor Bridge v6 Candidate ${source_commit:0:12}" \
LOCAL_EXECUTOR_LABEL="$label" \
INSTALL_DIR="$install" CONFIG_DIR="$(dirname "$config")" STATE_DIR="$state" PLIST="$plist" \
bash shell_bridge/install.sh --stage-only
printf '\n=== Local v6 candidate doctor ===\n'
python3 "$install/bridge.py" doctor --config "$config"
printf '\n=== Isolated candidate start ===\n'
launchctl bootstrap "gui/$(id -u)" "$plist"
launchctl print "gui/$(id -u)/$label" >/dev/null || {
  echo "HOLD: candidate bootstrap did not register; inspect isolated logs" >&2; exit 8
}
printf '\n=== Exact-build live heartbeat qualification ===\n'
root_id="$(python3 - "$config" <<'PYROOT'
import json,sys
cfg=json.load(open(sys.argv[1],encoding='utf-8'))
root=cfg.get("drive_root_folder_id")
if not isinstance(root,str) or not root or any(ch.isspace() for ch in root):
    raise SystemExit("HOLD: invalid staged Drive folder identity")
print(root)
PYROOT
)"
health_json="$(mktemp)"
trap 'rm -f "$report" "$health_json"' EXIT
qualified=0
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if rclone --drive-root-folder-id "$root_id" --timeout 20s copyto "${RCLONE_REMOTE}health.json" "$health_json" >/dev/null 2>&1 &&
    PYTHONDONTWRITEBYTECODE=1 python3 shell_bridge/verify_v6_candidate_health.py \
      "$config" "$health_json" "$source_commit" "$label" >/dev/null 2>&1; then
    qualified=1
    break
  fi
  sleep 2
done
if [ "$qualified" -ne 1 ]; then
  echo "HOLD: candidate has not published a fresh exact-build heartbeat with its live launchd PID." >&2
  echo "Only isolated staging was changed; inspect $state/stderr.log before continuing." >&2
  exit 9
fi
PYTHONDONTWRITEBYTECODE=1 python3 shell_bridge/verify_v6_candidate_health.py \
  "$config" "$health_json" "$source_commit" "$label"
printf '\nISOLATED_V6_RUNNING=1\nLABEL=%s\nCONFIG=%s\nSOURCE=%s\n' "$label" "$config" "$source_commit"
printf 'Production v5 unchanged. Cutover still requires live approval, replay and recovery acceptance.\n'
