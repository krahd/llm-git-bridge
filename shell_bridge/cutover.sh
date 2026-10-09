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
RETIRE_OLD_AFTER_SMOKE="${RETIRE_OLD_AFTER_SMOKE:-0}"
UID_NOW="$(id -u)"
ROLLBACK_FILE=""
TMP_REQ=""
TMP_RESULT=""
TMP_HEALTH=""
CUTOVER_COMMITTED=0
PROVISIONAL_ACTIVATION=0
STARTED_NEW=0
OLD_CONSUMERS_STOPPED=0

fail(){ echo "ERROR: $*" >&2; exit 1; }
need(){ command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"; }

rollback() {
  rc=$?
  if [ "$rc" -ne 0 ] && [ "$CUTOVER_COMMITTED" -eq 0 ]; then
    if [ "$STARTED_NEW" -eq 1 ]; then
      # v6 might have executed an effect before publishing its result. A
      # successful process shutdown is not evidence that its journal is safe
      # for v5. Hold both consumers rather than blindly replaying requests.
      launchctl bootout "gui/${UID_NOW}/${NEW_LABEL}" >/dev/null 2>&1 || true
      mkdir -p "$STATE_DIR"; chmod 700 "$STATE_DIR" || true
      printf 'HOLD: v6 admitted a request or could have; do not restart v5 until a journal/mailbox reconciliation proves no duplicate effects.\n' >&2
      printf '%s\n' "cutover_holding_reconciliation $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATE_DIR/cutover-reconciliation-required"
      if [ -n "$ROLLBACK_FILE" ] && [ -s "$ROLLBACK_FILE" ]; then
        cp -p "$ROLLBACK_FILE" "$STATE_DIR/cutover-rollback-services.tsv"
        chmod 600 "$STATE_DIR/cutover-rollback-services.tsv"
      fi
    elif [ "$OLD_CONSUMERS_STOPPED" -eq 1 ]; then
      # No v6 admission was possible; restoring the original consumer does
      # not introduce a v6/v5 duplicate-execution race.
      echo "Cutover failed after stopping legacy consumers; restoring previously active bridge service(s)." >&2
      if [ -n "$ROLLBACK_FILE" ] && [ -f "$ROLLBACK_FILE" ]; then
        while IFS='|' read -r label plist; do
          [ -n "$label" ] || continue
          if ! launchctl print "gui/${UID_NOW}/${label}" >/dev/null 2>&1; then
            launchctl bootstrap "gui/${UID_NOW}" "$plist" >/dev/null 2>&1 || true
            launchctl kickstart -k "gui/${UID_NOW}/${label}" >/dev/null 2>&1 || true
          fi
        done < "$ROLLBACK_FILE"
      fi
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
[ "$RETIRE_OLD_AFTER_SMOKE" = 0 ] || fail "legacy retirement requires separate verified production acceptance; RETIRE_OLD_AFTER_SMOKE must be 0"
[ -f "$NEW_PLIST" ] || fail "staged v6 plist missing: $NEW_PLIST"
[ -f "$NEW_CONFIG" ] || fail "staged v6 config missing: $NEW_CONFIG"
[ -f "$NEW_INSTALL_DIR/bridge.py" ] || fail "staged v6 bridge missing: $NEW_INSTALL_DIR/bridge.py"
[ -f "$NEW_INSTALL_DIR/workspace.py" ] || fail "staged v6 coordinator missing: $NEW_INSTALL_DIR/workspace.py"
[ -f "$NEW_INSTALL_DIR/approval_helper.py" ] || fail "staged v6 approval helper missing: $NEW_INSTALL_DIR/approval_helper.py"
[ -f "$NEW_INSTALL_DIR/install-manifest.json" ] || fail "staged v6 install manifest missing: $NEW_INSTALL_DIR/install-manifest.json"

python3 - "$NEW_INSTALL_DIR/install-manifest.json" "$NEW_INSTALL_DIR/bridge.py" "$NEW_INSTALL_DIR/workspace.py" "$NEW_INSTALL_DIR/approval_helper.py" "$NEW_CONFIG" "$NEW_PLIST" "$NEW_INSTALL_DIR/Local Executor Approval.app/Contents/Info.plist" "$NEW_INSTALL_DIR/Local Executor Approval.app/Contents/MacOS/local-executor-approval" <<'PYMANIFEST' || fail "staged v6 install manifest integrity check failed"
import hashlib,json,sys
manifest_path,bridge_path,workspace_path,approval_helper_path,config_path,plist_path,app_info_path,app_exec_path=sys.argv[1:]
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
    'config_sha256':sha256(config_path),
    'launchagent_plist_sha256':sha256(plist_path),
    'approval_app_info_sha256':sha256(app_info_path),
    'approval_app_executable_sha256':sha256(app_exec_path),
}
for key,value in expected.items():
    if m.get(key) != value: raise SystemExit(5)
PYMANIFEST

STAGED_SOURCE_COMMIT="$(python3 - "$NEW_INSTALL_DIR/install-manifest.json" <<'PYSOURCE'
import json,sys
m=json.load(open(sys.argv[1],encoding='utf-8'))
print(m.get('source_commit',''))
PYSOURCE
)"
[ -n "${EXPECTED_SOURCE_COMMIT:-}" ] || fail "EXPECTED_SOURCE_COMMIT is required for production cutover"
[ "$STAGED_SOURCE_COMMIT" = "$EXPECTED_SOURCE_COMMIT" ] || fail "staged source commit $STAGED_SOURCE_COMMIT does not match EXPECTED_SOURCE_COMMIT=$EXPECTED_SOURCE_COMMIT"

IFS=$'\t' read -r REMOTE ROOT_ID ALLOWED_ROOT STATE_DIR TARGET_INSTANCE < <(python3 - "$NEW_CONFIG" <<'PYCFG'
import json,sys
c=json.load(open(sys.argv[1],encoding='utf-8'))
vals=[c.get('remote',''),c.get('drive_root_folder_id',''),c.get('allowed_root',''),c.get('state_dir',''),c.get('bridge_instance_id','')]
if not all(isinstance(x,str) and x for x in vals): raise SystemExit(2)
print('\t'.join(vals))
PYCFG
 ) || fail "staged v6 config is incomplete"

# A bridge request may control cutover only when it comes from a proven different
# mailbox/instance/state directory. A true out-of-band process has no request ID.
if [ -n "${CHATGPT_SHELL_BRIDGE_REQUEST_ID:-}" ]; then
  [ -n "${LOCAL_EXECUTOR_CALLER_BRIDGE_INSTANCE_ID:-}" ] || fail "bridge-controlled cutover is missing caller bridge instance identity"
  [ -n "${LOCAL_EXECUTOR_CALLER_DRIVE_ROOT_FOLDER_ID:-}" ] || fail "bridge-controlled cutover is missing caller Drive root identity"
  [ -n "${LOCAL_EXECUTOR_CALLER_STATE_DIR:-}" ] || fail "bridge-controlled cutover is missing caller state directory identity"
  [ "$LOCAL_EXECUTOR_CALLER_DRIVE_ROOT_FOLDER_ID" != "$ROOT_ID" ] || fail "cutover controller is attached to the target production mailbox"
  [ "$LOCAL_EXECUTOR_CALLER_BRIDGE_INSTANCE_ID" != "$TARGET_INSTANCE" ] || fail "cutover controller is the target production bridge instance"
  [ "$LOCAL_EXECUTOR_CALLER_STATE_DIR" != "$STATE_DIR" ] || fail "cutover controller shares the target production state directory"
fi

if launchctl print "gui/${UID_NOW}/${NEW_LABEL}" >/dev/null 2>&1; then
  fail "v6 service is already loaded; reconcile the existing v6 service before cutover"
fi

EXPECTED_APPROVAL_APP="$NEW_INSTALL_DIR/Local Executor Approval.app"
CONFIG_APPROVAL_APP="$(python3 - "$NEW_CONFIG" <<'PYAPP'
import json,sys
c=json.load(open(sys.argv[1],encoding='utf-8'))
v=c.get('operator_approval_app','')
print(v if isinstance(v,str) else '')
PYAPP
)"
[ "$CONFIG_APPROVAL_APP" = "$EXPECTED_APPROVAL_APP" ] || fail "staged v6 approval app path does not match install directory"
[ -x "$EXPECTED_APPROVAL_APP/Contents/MacOS/local-executor-approval" ] || fail "staged v6 approval app executable missing"

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
import datetime,json,sys,time
h=json.load(open(sys.argv[1],encoding='utf-8'))
active=h.get('active_requests')
if not isinstance(active,list): raise SystemExit('health.json has no active_requests list')
if active: raise SystemExit('production bridge is not quiescent; active requests: '+', '.join(map(str,active)))
pending=h.get('pending_approvals', [])
if not isinstance(pending,list): raise SystemExit('health.json pending_approvals is invalid')
if pending: raise SystemExit('production bridge has pending approvals: '+', '.join(map(str,pending)))
state=h.get('state')
if not isinstance(state,dict) or state.get('started_without_finished') != 0:
    raise SystemExit('production bridge has STARTED-without-FINISHED work; refusing cutover')
raw=h.get('updated_at')
if not isinstance(raw,str) or not raw: raise SystemExit('health.json has no updated_at timestamp')
try: ts=datetime.datetime.fromisoformat(raw.replace('Z','+00:00')).timestamp()
except Exception: raise SystemExit('health.json updated_at is invalid')
now=time.time()
if ts > now + 30: raise SystemExit('health.json timestamp is implausibly in the future')
stale=h.get('health_stale_after_seconds',120)
try: stale=float(stale)
except Exception: stale=120.0
max_age=max(180.0, stale + 60.0)
if now-ts > max_age: raise SystemExit(f'production health.json is stale ({now-ts:.1f}s old); refusing cutover without fresh quiescence evidence')
PYHEALTH

if [ -d "$LEGACY_STATE_DIR/approvals/pending" ]; then
  shopt -s nullglob
  LEGACY_PENDING_APPROVALS=("$LEGACY_STATE_DIR/approvals/pending/"*.json)
  shopt -u nullglob
  [ "${#LEGACY_PENDING_APPROVALS[@]}" -eq 0 ] || fail "legacy production state has pending approval records; reconcile them before cutover"
fi

PENDING="$("${R[@]}" lsf "${REMOTE}requests" --files-only 2>/dev/null | sed '/^[[:space:]]*$/d' | head -n 1 || true)"
[ -z "$PENDING" ] || fail "production request mailbox is not empty; finish or reconcile pending requests first"

ROLLBACK_FILE="$(mktemp)"
for label in "${OLD_LABELS[@]}"; do
  plist="$HOME/Library/LaunchAgents/$label.plist"
  if launchctl print "gui/${UID_NOW}/${label}" >/dev/null 2>&1; then
    [ -f "$plist" ] || fail "loaded legacy service has no rollback plist: $label ($plist)"
    printf '%s|%s\n' "$label" "$plist" >> "$ROLLBACK_FILE"
  fi
done

# Stop old consumers first. Only then may v6 attach to the same production mailbox.
for label in "${OLD_LABELS[@]}"; do
  if launchctl print "gui/${UID_NOW}/${label}" >/dev/null 2>&1; then
    launchctl bootout "gui/${UID_NOW}/${label}" || fail "failed to stop legacy service: $label"
    OLD_CONSUMERS_STOPPED=1
  fi
done
for label in "${OLD_LABELS[@]}"; do
  if launchctl print "gui/${UID_NOW}/${label}" >/dev/null 2>&1; then
    fail "legacy service is still loaded after bootout: $label"
  fi
done

# Close the drain race: after legacy consumers are stopped, refuse to attach v6
# if a request arrived while the stop sequence was in progress. Rollback will
# restore any legacy consumers that were actually stopped.
PENDING_AFTER_STOP="$("${R[@]}" lsf "${REMOTE}requests" --files-only 2>/dev/null | sed '/^[[:space:]]*$/d' | head -n 1 || true)"
[ -z "$PENDING_AFTER_STOP" ] || fail "a request arrived during cutover drain; restoring legacy consumers before retry"

launchctl bootstrap "gui/${UID_NOW}" "$NEW_PLIST"
STARTED_NEW=1
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
# A harmless shell smoke only proves transport, not replay/approval/Git
# acceptance. Activation remains provisional until a separate, independently
# verified acceptance operation; preserve its smoke result as evidence.
PROVISIONAL_ACTIVATION=1
if [ "$RETIRE_OLD_AFTER_SMOKE" -eq 1 ]; then
  fail "legacy retirement is forbidden during provisional cutover; complete independent production acceptance first"
fi
# Keep a durable rollback-service list and explicit provisional state.
mkdir -p "$STATE_DIR"; chmod 700 "$STATE_DIR" || true
if [ -s "$ROLLBACK_FILE" ]; then
  cp -p "$ROLLBACK_FILE" "$STATE_DIR/cutover-rollback-services.tsv"
  chmod 600 "$STATE_DIR/cutover-rollback-services.tsv"
fi
printf 'source=%s\nlabel=%s\nsmoke_id=%s\nstate=provisional\n' "$STAGED_SOURCE_COMMIT" "$NEW_LABEL" "$RID" > "$STATE_DIR/cutover-provisional.txt"
chmod 600 "$STATE_DIR/cutover-provisional.txt"
CUTOVER_COMMITTED=1

# No automatic retirement or replacement of legacy coordinator files.
# Production acceptance is separate from activation and cannot be inferred
# from a read-only smoke. Preserve all legacy plists, journals and checkpoints.
trap - EXIT
rm -f "$ROLLBACK_FILE" "$TMP_REQ" "$TMP_RESULT" "$TMP_HEALTH"
printf 'LOCAL_EXECUTOR_BRIDGE_CUTOVER_PROVISIONAL=1\n'
printf 'LABEL: %s\n' "$NEW_LABEL"
printf 'RETIRED_OLD: 0\n'
printf 'CONFIG: %s\n' "$NEW_CONFIG"
printf 'WORKSPACE: %s\n' "$NEW_INSTALL_DIR/workspace.py"
