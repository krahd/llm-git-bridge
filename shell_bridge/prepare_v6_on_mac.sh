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
bash -n shell_bridge/install.sh shell_bridge/cutover.sh shell_bridge/migrate_v6_staging_atomic.sh
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
  echo "HOLD: RCLONE_REMOTE must explicitly name your existing configured Drive remote (e.g. chatgpt-git-bridge:)." >&2
  exit 7
fi
ALLOWED_ROOT="${ALLOWED_ROOT:-$HOME/tom-repos}" \
BASE_PATH="Local Executor Bridge v6 Candidate ${source_commit:0:12}" \
LOCAL_EXECUTOR_LABEL="$label" \
INSTALL_DIR="$install" CONFIG_DIR="$(dirname "$config")" STATE_DIR="$state" PLIST="$plist" \
bash shell_bridge/install.sh --stage-only
printf '\nSTAGED_NOT_STARTED=1\nLABEL=%s\nCONFIG=%s\nSOURCE=%s\n' "$label" "$config" "$source_commit"
printf 'Production v5 unchanged. Do not cut over until the real approval and recovery acceptance gates pass.\n'
