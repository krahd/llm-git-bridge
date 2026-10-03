#!/bin/bash
set -euo pipefail

NEW_LABEL="${LOCAL_EXECUTOR_LABEL:-net.laurenzo.local-executor-bridge}"
NEW_PLIST="${NEW_PLIST:-$HOME/Library/LaunchAgents/$NEW_LABEL.plist}"
NEW_INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/share/local-executor-bridge}"
NEW_CONFIG="${CONFIG_FILE:-$HOME/.config/local-executor-bridge/config.json}"
LEGACY_INSTALL_DIR="${LEGACY_INSTALL_DIR:-$HOME/.local/share/chatgpt-shell-bridge}"
LEGACY_STATE_DIR="${LEGACY_STATE_DIR:-$HOME/.local/state/chatgpt-shell-bridge}"
OLD_LABELS=("com.tom.chatgpt-shell-bridge" "io.llm-git-bridge.daemon" "net.laurenzo.mac-executor-bridge")
SMOKE_ATTEMPTS="${SMOKE_ATTEMPTS:-30}"
SMOKE_SLEEP="${SMOKE_SLEEP:-2}"
UID_NOW="$(id -u)"
ROLLBACK_FILE=""
TMP_REQ=""
TMP_RESULT=""
TMP_HEALTH=""
CUTOVER_COMMITTED=0

fail(){ echo "ERROR: $*" >&2; exit 1; }
need(){ command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"; }

rollback() {
  rc=$?
  if [ "$rc" -ne 0 ] && [ "$CUTOVER_COMMITTED" -eq 0 ]; then
    echo "Cutover failed before commit; restoring previously active bridge service(s)." >&2
    launchctl bootout "gui/${UID_NOW}/${NEW_LABEL}" >/dev/null 2>&1 || true
    if [ -n "$ROLLBACK_FILE" ] && [ -f "$ROLLBACK_FILE" ]; then
      while IFS='|' read -r label plist; do
        [ -n "$label" ] || continue
        if [ -f "$plist" ]; then
          launchctl bootstrap "gui/${UID_NOW}" "$plist" >/dev/null 2>&1 || true
          launchctl kickstart -k "gui/${UID_NOW}/${label}" >/dev/null 2>&1 || true
        fi
      done < "$ROLLBACK_FILE"
    fi
  fi
  [ -n "$ROLLBACK_FILE" ] && rm -f "$ROLLBACK_FILE" || true
  [ -n "$TMP_REQ" ] && rm -f "$TMP_REQ" || true
  [ -n "$TMP_RESULT" ] && rm -f "$TMP_RESULT" || true
  [ -n "$TMP_HEALTH" ] && rm -f "$TMP_HEALTH" || true
  exit "$rc"
}
trap rollback EXIT

[ "$(uname -s)" = Darwin ] || fail "macOS required"
need python3; need rclone; need launchctl
[ -f "$NEW_PLIST" ] || fail "staged v6 plist missing: $NEW_PLIST"
[ -f "$NEW_CONFIG" ] || fail "staged v6 config missing: $NEW_CONFIG"
[ -f "$NEW_INSTALL_DIR/bridge.py" ] || fail "staged v6 bridge missing: $NEW_INSTALL_DIR/bridge.py"
[ -f "$NEW_INSTALL_DIR/workspace.py" ] || fail "staged v6 coordinator missing: $NEW_INSTALL_DIR/workspace.py"
[ -f "$NEW_INSTALL_DIR/approval_helper.py" ] || fail "staged v6 approval helper missing: $NEW_INSTALL_DIR/approval_helper.py"
[ -f "$NEW_INSTALL_DIR/install-manifest.json" ] || fail "staged v6 install manifest missing: $NEW_INSTALL_DIR/install-manifest.json"

python3 - "$NEW_INSTALL_DIR/install-manifest.json" "$NEW_INSTALL_DIR/bridge.py" "$NEW_INSTALL_DIR/workspace.py" "$NEW_INSTALL_DIR/approval_helper.py" <<'PYMANIFEST' || fail "staged v6 install manifest integrity check failed"
import hashlib,json,sys
manifest_path,bridge_path,workspace_path,approval_helper_path=sys.argv[1:]
def sha256(path):
    with open(path,'rb') as f: return hashlib.sha256(f.read()).hexdigest()
try:
    m=json.load(open(manifest_path,encoding='utf-8'))
except Exception:
    raise SystemExit(2)
if m.get('schema') != 1: raise SystemExit(3)
source=m.get('source_commit')
if not isinstance(source,str) or len(source)!=40 or any(c not in '0123456789abcdef' for c in source.lower()): raise SystemExit(4)
expected={
    'bridge_sha256':sha256(bridge_path),
    'workspace_sha256':sha256(workspace_path),
    'approval_helper_sha256':sha256(approval_helper_path),
}
for key,value in expected.items():
    if m.get(key) != value: raise SystemExit(5)
PYMANIFEST

# This script is intentionally out-of-band. Running it as a request through the
# bridge being replaced would make that request itself an active consumer while
# launchctl tears down its parent daemon.
if [ -n "${CHATGPT_SHELL_BRIDGE_REQUEST_ID:-}" ]; then
  fail "cutover must be run out-of-band (for example from Terminal), not through the live bridge"
fi

IFS=$'\t' read -r REMOTE ROOT_ID ALLOWED_ROOT STATE_DIR < <(python3 - "$NEW_CONFIG" <<'PYCFG'
import json,sys
c=json.load(open(sys.argv[1],encoding='utf-8'))
vals=[c.get('remote',''),c.get('drive_root_folder_id',''),c.get('allowed_root',''),c.get('state_dir','')]
if not all(isinstance(x,str) and x for x in vals): raise SystemExit(2)
print('\t'.join(vals))
PYCFG
 ) || fail "staged v6 config is incomplete"

EXPECTED_APPROVAL_APP="$NEW_INSTALL_DIR/Local Executor Approval.app"
CONFIG_APPROVAL_APP="$(python3 - "$NEW_CONFIG" <<'PYAPP'
import json,sys
c=json.load(open(sys.argv[1],encoding='utf-8'))
v=c.get('operator_approval_app','')
print(v if isinstance(v,str) else '')
PYAPP
)"
[ "$CONFIG_APPROVAL_APP" = "$EXPECTED_APPROVAL_APP" ] || fail "staged v6 approval app path does not match install directory"
[ -x "$EXPECTED_APPROVAL_APP/Contents/MacOS/approval-helper" ] || fail "staged v6 approval app executable missing"

# Existing durable workspace state must survive an in-place v5 upgrade.
if [ -d "$LEGACY_STATE_DIR/workspaces" ]; then
  SAME_STATE="$(python3 - "$STATE_DIR" "$LEGACY_STATE_DIR" <<'PYSTATE'
from pathlib import Path
import sys
print('1' if Path(sys.argv[1]).expanduser().resolve()==Path(sys.argv[2]).expanduser().resolve() else '0')
PYSTATE
)"
  [ "$SAME_STATE" = 1 ] || fail "v5 workspace state exists; stage v6 with STATE_DIR=$LEGACY_STATE_DIR before cutover"
fi

R=(rclone --drive-root-folder-id "$ROOT_ID")
TMP_HEALTH="$(mktemp)"
"${R[@]}" copyto "${REMOTE}health.json" "$TMP_HEALTH" >/dev/null 2>&1 || fail "cannot read current production health.json"
python3 - "$TMP_HEALTH" <<'PYHEALTH'
import json,sys
h=json.load(open(sys.argv[1],encoding='utf-8'))
active=h.get('active_requests')
if not isinstance(active,list): raise SystemExit('health.json has no active_requests list')
if active: raise SystemExit('production bridge is not quiescent; active requests: '+', '.join(map(str,active)))
PYHEALTH

PENDING="$("${R[@]}" lsf "${REMOTE}requests" --files-only 2>/dev/null | sed '/^[[:space:]]*$/d' | head -n 1 || true)"
[ -z "$PENDING" ] || fail "production request mailbox is not empty; finish or reconcile pending requests first"

ROLLBACK_FILE="$(mktemp)"
for label in "${OLD_LABELS[@]}"; do
  plist="$HOME/Library/LaunchAgents/$label.plist"
  if launchctl print "gui/${UID_NOW}/${label}" >/dev/null 2>&1; then
    printf '%s|%s\n' "$label" "$plist" >> "$ROLLBACK_FILE"
  fi
done

# Stop old consumers first. Only then may v6 attach to the same production mailbox.
for label in "${OLD_LABELS[@]}"; do
  launchctl bootout "gui/${UID_NOW}/${label}" >/dev/null 2>&1 || true
done
launchctl bootout "gui/${UID_NOW}/${NEW_LABEL}" >/dev/null 2>&1 || true
launchctl bootstrap "gui/${UID_NOW}" "$NEW_PLIST"
launchctl kickstart -k "gui/${UID_NOW}/${NEW_LABEL}"
launchctl print "gui/${UID_NOW}/${NEW_LABEL}" >/dev/null || fail "v6 LaunchAgent did not start"
python3 "$NEW_INSTALL_DIR/bridge.py" doctor --config "$NEW_CONFIG" >/dev/null || fail "v6 doctor failed after service switch"

# End-to-end production smoke: at this point v6 is the sole mailbox consumer.
RID="cutover-smoke-$(date +%Y%m%d%H%M%S)-$$"
TMP_REQ="$(mktemp)"; TMP_RESULT="$(mktemp)"
python3 - "$TMP_REQ" "$RID" "$ALLOWED_ROOT" <<'PYREQ'
import json,sys
path,rid,root=sys.argv[1:]
req={
  'protocol':1,'id':rid,'cwd':root,
  'explanation':'Verify the newly cut-over Local Executor Bridge v6 can execute a harmless production smoke test.',
  'command':"printf 'LOCAL_EXECUTOR_BRIDGE_OK\n'; git --version; pwd",
  'timeout_seconds':30,'write_scope':'read_only',
}
with open(path,'w',encoding='utf-8') as f:
    json.dump(req,f,separators=(',',':'),sort_keys=True); f.write('\n')
PYREQ
"${R[@]}" copyto "$TMP_REQ" "${REMOTE}requests/${RID}.json"
FOUND=0
for ((i=0;i<SMOKE_ATTEMPTS;i++)); do
  if "${R[@]}" copyto "${REMOTE}results/${RID}.json" "$TMP_RESULT" >/dev/null 2>&1; then FOUND=1; break; fi
  sleep "$SMOKE_SLEEP"
done
[ "$FOUND" -eq 1 ] || fail "v6 production smoke produced no result"
python3 - "$TMP_RESULT" <<'PYRESULT'
import json,sys
r=json.load(open(sys.argv[1],encoding='utf-8'))
if r.get('status')!='completed' or r.get('exit_code')!=0 or 'LOCAL_EXECUTOR_BRIDGE_OK' not in r.get('stdout_text',''):
    print(json.dumps(r,indent=2),file=sys.stderr); raise SystemExit('v6 production smoke failed')
PYRESULT
"${R[@]}" deletefile "${REMOTE}results/${RID}.json" >/dev/null 2>&1 || true

# Success boundary. Preserve compatibility for clients that still invoke the
# historical coordinator path, but make it resolve to the bundled v6 coordinator.
archive_dir="$STATE_DIR/retired-v5-runtime"
mkdir -p "$archive_dir" "$LEGACY_INSTALL_DIR"
chmod 700 "$archive_dir" "$LEGACY_INSTALL_DIR" || true
if [ -e "$LEGACY_INSTALL_DIR/workspace.py" ] && [ ! -L "$LEGACY_INSTALL_DIR/workspace.py" ]; then
  cp -p "$LEGACY_INSTALL_DIR/workspace.py" "$archive_dir/workspace.py.$(date +%Y%m%d%H%M%S)"
fi
ln -sfn "$NEW_INSTALL_DIR/workspace.py" "$LEGACY_INSTALL_DIR/workspace.py"

# Archive obsolete service plists only after the v6 production smoke passes.
archive_plists="$STATE_DIR/retired-launchagents"
mkdir -p "$archive_plists"; chmod 700 "$archive_plists" || true
for label in "${OLD_LABELS[@]}"; do
  plist="$HOME/Library/LaunchAgents/$label.plist"
  if [ -f "$plist" ]; then
    mv "$plist" "$archive_plists/${label}.$(date +%Y%m%d%H%M%S).plist"
  fi
done

CUTOVER_COMMITTED=1
trap - EXIT
rm -f "$ROLLBACK_FILE" "$TMP_REQ" "$TMP_RESULT" "$TMP_HEALTH"
printf 'LOCAL_EXECUTOR_BRIDGE_CUTOVER=1\n'
printf 'LABEL: %s\n' "$NEW_LABEL"
printf 'CONFIG: %s\n' "$NEW_CONFIG"
printf 'WORKSPACE: %s\n' "$NEW_INSTALL_DIR/workspace.py"
