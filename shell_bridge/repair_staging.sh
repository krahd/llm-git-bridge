#!/bin/bash
set -euo pipefail

REPO="/Users/tom/tom-repos/projects/llm-git-bridge"
LABEL="net.laurenzo.local-executor-bridge-v6-staging"
DRIVE_ROOT="15ql2yACOq7H6qo0IgzosySqW8nHgUKXv"
BASE_PATH="Local Executor Bridge v6 Staging"
REQ_ID="101ZPUf3ZLCG2BW8HLgQazg48TNyUMrNa"
RES_ID="1xnU55UNAc-6APc6b-8naPNyk4DlUC3xd"
STATE_DIR="$HOME/.local/state/local-executor-bridge-v6-staging"
CURRENT_INSTALL="$HOME/.local/share/local-executor-bridge-v6-staging"
CURRENT_CONFIG="$HOME/.config/local-executor-bridge-v6-staging/config.json"
CURRENT_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
EXPECTED="${EXPECTED_SOURCE_COMMIT:?EXPECTED_SOURCE_COMMIT required}"
[ "${EXPECTED_STAGING_LABEL:-}" = "$LABEL" ] || { echo "unexpected staging label" >&2; exit 2; }
[ "${EXPECTED_STAGING_DRIVE_ROOT:-}" = "$DRIVE_ROOT" ] || { echo "unexpected staging Drive root" >&2; exit 2; }
[ "$(git -C "$REPO" rev-parse HEAD)" = "$EXPECTED" ] || { echo "candidate commit changed" >&2; exit 2; }
[ -z "$(git -C "$REPO" status --porcelain --untracked-files=no -- shell_bridge/bridge.py shell_bridge/workspace.py shell_bridge/approval_helper.py shell_bridge/approval_gui.swift shell_bridge/install.sh shell_bridge/cutover.sh shell_bridge/repair_staging.sh shell_bridge/bridge_repair.swift)" ] || { echo "audited bridge sources are dirty" >&2; exit 2; }
for p in "$CURRENT_INSTALL" "$HOME/.config/local-executor-bridge-v6-staging" "$STATE_DIR"; do
  [ ! -L "$p" ] || { echo "refusing symlink staging path: $p" >&2; exit 2; }
done
uid="$(id -u)"
stamp="$(date +%Y%m%d%H%M%S)"
new_install="$HOME/.local/share/local-executor-bridge-v6-staging-$EXPECTED"
new_config_dir="$HOME/.config/local-executor-bridge-v6-staging-$EXPECTED"
new_plist="$HOME/Library/LaunchAgents/$LABEL.candidate-$EXPECTED.plist"
backup="$STATE_DIR/repair-backup-$stamp"
mkdir -p "$backup"
cp "$CURRENT_PLIST" "$backup/old.plist"
cp "$CURRENT_CONFIG" "$backup/old-config.json"
old_install_target="$CURRENT_INSTALL"
rollback(){ rc=$?; if [ "$rc" -ne 0 ]; then launchctl bootout "gui/$uid/$LABEL" >/dev/null 2>&1 || true; cp "$backup/old.plist" "$CURRENT_PLIST"; cp "$backup/old-config.json" "$CURRENT_CONFIG"; launchctl bootstrap "gui/$uid" "$CURRENT_PLIST" >/dev/null 2>&1 || true; launchctl kickstart -k "gui/$uid/$LABEL" >/dev/null 2>&1 || true; fi; exit "$rc"; }
trap rollback EXIT

rm -rf "$new_install" "$new_config_dir" "$new_plist"
INSTALL_DIR="$new_install" CONFIG_DIR="$new_config_dir" STATE_DIR="$STATE_DIR" PLIST="$new_plist" LOCAL_EXECUTOR_LABEL="$LABEL" ALLOWED_ROOT="/Users/tom/tom-repos" BASE_PATH="$BASE_PATH" RCLONE_REMOTE="chatgpt-git-bridge" "$REPO/shell_bridge/install.sh" --stage-only >/tmp/local-executor-staging-repair-stage.out
python3 - "$new_config_dir/config.json" "$DRIVE_ROOT" "$REQ_ID" "$RES_ID" <<'PYCFG'
import json,sys
p,root,req,res=sys.argv[1:]
c=json.load(open(p))
assert c['drive_root_folder_id']==root
assert c['requests_folder_id']==req
assert c['results_folder_id']==res
assert c['base_path']=='Local Executor Bridge v6 Staging'
assert c['state_dir'].endswith('/.local/state/local-executor-bridge-v6-staging')
PYCFG
python3 - "$new_install/install-manifest.json" "$EXPECTED" <<'PYMAN'
import json,sys
m=json.load(open(sys.argv[1])); assert m['source_commit']==sys.argv[2]
PYMAN
launchctl bootout "gui/$uid/$LABEL" >/dev/null 2>&1 || true
cp "$new_config_dir/config.json" "$CURRENT_CONFIG"
cp "$new_plist" "$CURRENT_PLIST"
launchctl bootstrap "gui/$uid" "$CURRENT_PLIST"
launchctl kickstart -k "gui/$uid/$LABEL"
sleep 2
launchctl print "gui/$uid/$LABEL" >/dev/null
python3 "$new_install/bridge.py" doctor --config "$CURRENT_CONFIG" >/dev/null
# Harmless direct smoke against isolated mailbox: bridge health must publish current v6 identity.
tmp="$(mktemp)"; rclone --drive-root-folder-id "$DRIVE_ROOT" copyto "chatgpt-git-bridge:health.json" "$tmp" >/dev/null 2>&1
python3 - "$tmp" <<'PYH'
import json,sys,time,datetime
h=json.load(open(sys.argv[1])); assert str(h.get('bridge_version'))=='6'; assert h.get('drive_root_folder_id')=='15ql2yACOq7H6qo0IgzosySqW8nHgUKXv'; assert h.get('state',{}).get('started_without_finished')==0
PYH
rm -f "$tmp"
mkdir -p "$STATE_DIR/bootstrap-audit"
printf '%s\n' "source_commit=$EXPECTED" "installed_runtime=$new_install" "completed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATE_DIR/bootstrap-audit/repair-$stamp.txt"
trap - EXIT
echo "STAGING_REPAIR_OK=1"
echo "SOURCE_COMMIT=$EXPECTED"
echo "RUNTIME=$new_install"
