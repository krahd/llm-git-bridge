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
# Default remains the original v5-first cutover. Only the separate v6
# wrapper may explicitly select the v6-to-v6 handover mode.
CUTOVER_SOURCE_VERSION="${CUTOVER_SOURCE_VERSION:-5}"
UID_NOW="$(id -u)"
ROLLBACK_FILE=""
TMP_REQ=""
TMP_RESULT=""
TMP_HEALTH=""
CUTOVER_COMMITTED=0
PROVISIONAL_ACTIVATION=0
STARTED_NEW=0
OLD_CONSUMERS_STOPPED=0
V6_UNSAFE_TO_RESTORE=0
V5_UNSAFE_TO_RESTORE=0
V6_ARCHIVE_STARTED=0
V6_TARGET_PERSISTED=0
V6_OLD_LABEL=""
V6_OLD_SOURCE_PLIST=""
V6_ARCHIVE_DIR=""
V6_PERSIST_PLIST=""
V6_REGISTRAR="$(cd "$(dirname "$0")" && pwd)/v6_restart_registration.py"

fail(){ echo "ERROR: $*" >&2; exit 1; }
need(){ command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"; }

rollback() {
  rc=$?
  if [ "$rc" -ne 0 ] && [ "$CUTOVER_COMMITTED" -eq 0 ]; then
    if [ "$STARTED_NEW" -eq 1 ]; then
      if [ "$CUTOVER_SOURCE_VERSION" = 6 ] && [ "$V6_TARGET_PERSISTED" -eq 1 ]; then
        # Prevent an ambiguous new daemon from auto-starting again at next
        # login. The original source remains archived, not replayed.
        python3 "$V6_REGISTRAR" quarantine "$V6_PERSIST_PLIST" "$V6_ARCHIVE_DIR/failed-candidate-$NEW_LABEL.plist" "$NEW_LABEL" ||
          printf 'CRITICAL: v6 restart registration could not be quarantined.\n' >&2
      fi
      # v6 might have executed an effect before publishing its result. A
      # successful process shutdown is not evidence that its journal is safe
      # for v5. Hold both consumers rather than blindly replaying requests.
      launchctl bootout "gui/${UID_NOW}/${NEW_LABEL}" >/dev/null 2>&1 || true
      if launchctl print "gui/${UID_NOW}/${NEW_LABEL}" >/dev/null 2>&1; then
        printf "CRITICAL: v6 remains loaded after rollback bootout; do not restart any legacy mailbox consumer.\n" >&2
      fi
      mkdir -p "$STATE_DIR"; chmod 700 "$STATE_DIR" || true
      printf 'HOLD: v6 admitted a request or could have; do not restart v5 until a journal/mailbox reconciliation proves no duplicate effects.\n' >&2
      printf '%s\n' "cutover_holding_reconciliation $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATE_DIR/cutover-reconciliation-required"
      if [ -n "$ROLLBACK_FILE" ] && [ -s "$ROLLBACK_FILE" ]; then
        cp -p "$ROLLBACK_FILE" "$STATE_DIR/cutover-rollback-services.tsv"
        chmod 600 "$STATE_DIR/cutover-rollback-services.tsv"
      fi
    elif [ "$OLD_CONSUMERS_STOPPED" -eq 1 ]; then
      # The old v5 rollback previously restarted the consumer without
      # rechecking its journal or other mailbox owners after a failed
      # bootout. A process may have been interrupted mid-command even
      # though the pre-stop journal was clean. Enforce the same HOLD
      # semantics for v5 as for v6; never replay unknown effects.
      if [ "$CUTOVER_SOURCE_VERSION" = 5 ]; then
        V5_UNSAFE_TO_RESTORE=1
        if PYTHONDONTWRITEBYTECODE=1 python3 "$(cd "$(dirname "$0")" && pwd)/verify_quiescent_journal.py" "$LEGACY_STATE_DIR" >/dev/null &&
           PYTHONDONTWRITEBYTECODE=1 python3 "$(cd "$(dirname "$0")" && pwd)/verify_pending_approvals.py" "$LEGACY_STATE_DIR" >/dev/null; then
          V5_CURRENT_OWNERS="$(PYTHONDONTWRITEBYTECODE=1 python3 "$OWNER_SCRIPT")" || V5_CURRENT_OWNERS=""
          if [ -n "$V5_CURRENT_OWNERS" ]; then
            if python3 - "$V5_CURRENT_OWNERS" "$ROOT_ID" "${OLD_LABELS[@]}" <<'PYV5RESTORE'
import json,sys
inventory=json.loads(sys.argv[1]); root=sys.argv[2]; old=set(sys.argv[3:])
if inventory.get("safe_to_stage") is not True or inventory.get("problems"):
    raise SystemExit(1)
if any(r.get("drive_root_folder_id")==root and r.get("label") not in old
       for r in inventory.get("loaded",[])):
    raise SystemExit(1)
PYV5RESTORE
            then V5_UNSAFE_TO_RESTORE=0; fi
          fi
        fi
      fi
      # Even a failed bootout may have left indeterminate STARTED work.
      # Recheck current evidence before restoring old v6; an ambiguous
      # transition is a HOLD, not an invitation to replay effects.
      if [ "$CUTOVER_SOURCE_VERSION" = 6 ] && [ "$V6_UNSAFE_TO_RESTORE" -eq 0 ]; then
        if ! PYTHONDONTWRITEBYTECODE=1 python3 "$(cd "$(dirname "$0")" && pwd)/verify_quiescent_journal.py" "$LEGACY_STATE_DIR" >/dev/null; then
          V6_UNSAFE_TO_RESTORE=1
        fi
        if ! PYTHONDONTWRITEBYTECODE=1 python3 "$(cd "$(dirname "$0")" && pwd)/verify_pending_approvals.py" "$LEGACY_STATE_DIR" >/dev/null; then
          V6_UNSAFE_TO_RESTORE=1
        fi
        if [ "$V6_UNSAFE_TO_RESTORE" -eq 0 ]; then
          CURRENT_OWNERS="$(PYTHONDONTWRITEBYTECODE=1 python3 "$OWNER_SCRIPT")" ||
            V6_UNSAFE_TO_RESTORE=1
          if [ "$V6_UNSAFE_TO_RESTORE" -eq 0 ]; then
            python3 - "$CURRENT_OWNERS" "$ROOT_ID" "${OLD_LABELS[@]}" <<'PYROLLBACKOWN' || V6_UNSAFE_TO_RESTORE=1
import json,sys
inventory=json.loads(sys.argv[1])
root=sys.argv[2]
old=set(sys.argv[3:])
if inventory.get('safe_to_stage') is not True or inventory.get('problems'):
    raise SystemExit(1)
for item in inventory.get('loaded',[]):
    if item.get('drive_root_folder_id')==root and item.get('label') not in old:
        raise SystemExit(1)
PYROLLBACKOWN
          fi
        fi
      fi
      if [ "$CUTOVER_SOURCE_VERSION" = 6 ] && [ "$V6_UNSAFE_TO_RESTORE" -eq 0 ] && [ "$V6_ARCHIVE_STARTED" -eq 1 ]; then
        # Only before new-v6 admission, and after rechecking live journal,
        # approval queue and loaded owners, restore the original login plist.
        python3 "$V6_REGISTRAR" restore "$V6_ARCHIVE_DIR/$V6_OLD_LABEL.plist" "$V6_OLD_SOURCE_PLIST" "$V6_OLD_LABEL" ||
          V6_UNSAFE_TO_RESTORE=1
      fi
      if { [ "$CUTOVER_SOURCE_VERSION" = 6 ] && [ "$V6_UNSAFE_TO_RESTORE" -eq 1 ]; } ||
         { [ "$CUTOVER_SOURCE_VERSION" = 5 ] && [ "$V5_UNSAFE_TO_RESTORE" -eq 1 ]; }; then
        # An unresolved journal or another owner appeared after the source
        # stopped. Restarting either v5 or v6 could duplicate effects.
        mkdir -p "$STATE_DIR"; chmod 700 "$STATE_DIR" || true
        printf '%s\n' "pre_admission_source_restoration_hold $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATE_DIR/cutover-reconciliation-required"
        if [ -n "$ROLLBACK_FILE" ] && [ -s "$ROLLBACK_FILE" ]; then
          cp -p "$ROLLBACK_FILE" "$STATE_DIR/cutover-rollback-services.tsv"
          chmod 600 "$STATE_DIR/cutover-rollback-services.tsv"
        fi
        printf 'CRITICAL: source rollback evidence is unsafe or incomplete; keep old consumer stopped until journal/owner reconciliation.\n' >&2
      else
        # No new-v6 admission was possible and the last owner snapshot was
        # exclusive; restore only the original source service.
      echo "Cutover failed after stopping legacy consumers; restoring previously active bridge service(s)." >&2
      restore_failed=0
      if [ -n "$ROLLBACK_FILE" ] && [ -f "$ROLLBACK_FILE" ]; then
        while IFS='|' read -r label plist; do
          [ -n "$label" ] || continue
          if ! launchctl print "gui/${UID_NOW}/${label}" >/dev/null 2>&1; then
            launchctl bootstrap "gui/${UID_NOW}" "$plist" >/dev/null 2>&1 || true
            launchctl kickstart -k "gui/${UID_NOW}/${label}" >/dev/null 2>&1 || true
          fi
          if ! launchctl print "gui/${UID_NOW}/${label}" >/dev/null 2>&1; then
            printf 'ERROR: legacy rollback service did not restore: %s\n' "$label" >&2
            restore_failed=1
          fi
        done < "$ROLLBACK_FILE"
      else
        restore_failed=1
      fi
      if [ "$restore_failed" -ne 0 ]; then
        # Never report restoration success or discard recovery instructions
        # when a service remains absent after a pre-v6 failure.
        mkdir -p "$STATE_DIR"; chmod 700 "$STATE_DIR" || true
        printf 'HOLD: legacy service restoration incomplete; inspect LaunchAgents before resuming the mailbox.\n' >&2
        printf '%s\n' "legacy_rollback_incomplete $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATE_DIR/cutover-reconciliation-required"
        if [ -n "$ROLLBACK_FILE" ] && [ -s "$ROLLBACK_FILE" ]; then
          cp -p "$ROLLBACK_FILE" "$STATE_DIR/cutover-rollback-services.tsv"
          chmod 600 "$STATE_DIR/cutover-rollback-services.tsv"
        fi
      fi
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
case "$CUTOVER_SOURCE_VERSION" in
  5) ;;
  6)
    [ -t 0 ] || fail "v6-to-v6 handover requires out-of-band interactive local Terminal"
    [ -z "${CHATGPT_SHELL_BRIDGE_REQUEST_ID:-}" ] ||
      fail "v6-to-v6 handover cannot be submitted through any bridge mailbox"
    ;;
  *) fail "unsupported source version; use explicit v6-to-v6 wrapper" ;;
esac
[ -f "$NEW_PLIST" ] || fail "staged v6 plist missing: $NEW_PLIST"
[ -f "$NEW_CONFIG" ] || fail "staged v6 config missing: $NEW_CONFIG"
[ -f "$NEW_INSTALL_DIR/bridge.py" ] || fail "staged v6 bridge missing: $NEW_INSTALL_DIR/bridge.py"
[ -f "$NEW_INSTALL_DIR/workspace.py" ] || fail "staged v6 coordinator missing: $NEW_INSTALL_DIR/workspace.py"
[ -f "$NEW_INSTALL_DIR/approval_helper.py" ] || fail "staged v6 approval helper missing: $NEW_INSTALL_DIR/approval_helper.py"
[ -f "$NEW_INSTALL_DIR/trusted_operations.py" ] || fail "staged v6 trusted operations module missing: $NEW_INSTALL_DIR/trusted_operations.py"
[ -f "$NEW_INSTALL_DIR/bridge_mailbox_inspector.py" ] || fail "staged v6 mailbox inspector missing"
[ -f "$NEW_INSTALL_DIR/ssh_pinned_helper.py" ] || fail "staged v6 pinned SSH helper missing"
[ -f "$NEW_INSTALL_DIR/install-manifest.json" ] || fail "staged v6 install manifest missing: $NEW_INSTALL_DIR/install-manifest.json"

python3 - "$NEW_INSTALL_DIR/install-manifest.json" "$NEW_INSTALL_DIR/bridge.py" "$NEW_INSTALL_DIR/workspace.py" "$NEW_INSTALL_DIR/approval_helper.py" "$NEW_INSTALL_DIR/trusted_operations.py" "$NEW_INSTALL_DIR/bridge_mailbox_inspector.py" "$NEW_INSTALL_DIR/ssh_pinned_helper.py" "$NEW_CONFIG" "$NEW_PLIST" "$NEW_INSTALL_DIR/Local Executor Approval.app/Contents/Info.plist" "$NEW_INSTALL_DIR/Local Executor Approval.app/Contents/MacOS/local-executor-approval" <<'PYMANIFEST' || fail "staged v6 install manifest integrity check failed"
import hashlib,json,sys
manifest_path,bridge_path,workspace_path,approval_helper_path,trusted_path,inspector_path,ssh_path,config_path,plist_path,app_info_path,app_exec_path=sys.argv[1:]
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
    'trusted_operations_sha256':sha256(trusted_path),
    'bridge_mailbox_inspector_sha256':sha256(inspector_path),
    'ssh_pinned_helper_sha256':sha256(ssh_path),
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
# Discover actual loaded consumers rather than assuming static legacy labels
# are complete. Only known production labels may be stopped automatically;
# unrelated staged instances on separate mailboxes must be preserved.
OWNER_SCRIPT="$(cd "$(dirname "$0")" && pwd)/bridge_service_ownership.py"
[ -f "$OWNER_SCRIPT" ] || fail "live bridge ownership inspector is missing"
OWNERSHIP="$(python3 "$OWNER_SCRIPT")" || fail "loaded bridge ownership is not independently verified"
KNOWN_CONSUMERS="$(python3 - "$OWNERSHIP" "$ROOT_ID" "$NEW_LABEL" "$CUTOVER_SOURCE_VERSION" <<'PYOWNERS'
import json,sys
inventory=json.loads(sys.argv[1])
root,new,source_version=sys.argv[2:]
if inventory.get('problems') or inventory.get('safe_to_stage') is not True:
    raise SystemExit('HOLD: duplicate or unknown loaded bridge')
known={'com.tom.chatgpt-shell-bridge','io.llm-git-bridge.daemon',
       'net.laurenzo.mac-executor-bridge','net.laurenzo.local-executor-bridge'}
def allowed(label):
    if label in known:
        return True
    # SHA/suffix-specific v6 production labels are legitimate only as an
    # explicitly selected v6 source; never silently reclassify them as v5.
    return (source_version=='6'
            and label.startswith('net.laurenzo.local-executor-bridge-v6-')
            and len(label)>len('net.laurenzo.local-executor-bridge-v6-')
            and all(ch.isascii() and (ch.isalnum() or ch in '._-')
                    for ch in label))
owners=[r['label'] for r in inventory['loaded']
        if r.get('drive_root_folder_id')==root]
if not owners:
    raise SystemExit('HOLD: no loaded production mailbox consumer to transfer')
if new in owners or any(not allowed(label) for label in owners):
    raise SystemExit('HOLD: production mailbox consumer is not a recognized source service')
if source_version=='6' and len(owners)!=1:
    raise SystemExit('HOLD: v6 source must have exactly one loaded owner')
for label in owners:
    print(label)
PYOWNERS
)" || fail "production consumer enumeration failed"
OLD_LABELS=()
while IFS= read -r label; do
  [ -n "$label" ] && OLD_LABELS+=("$label")
done <<< "$KNOWN_CONSUMERS"
[ "${#OLD_LABELS[@]}" -gt 0 ] || fail "no verified production consumers to transfer"


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

# v5 request receipts are also deduplication evidence. The old check
# only enforced state reuse when a workspaces/ directory existed, permitting
# a seemingly clean v5->v6 cutover that silently forgot prior request IDs
# and their FINISHED/indeterminate outcomes. Refuse *any* state fork before
# a production service is stopped, even if no workspace has ever been made.
SAME_STATE="$(python3 - "$STATE_DIR" "$LEGACY_STATE_DIR" <<'PYSTATE'
from pathlib import Path
import sys
candidate,source=(Path(v).expanduser() for v in sys.argv[1:])
if not candidate.is_absolute() or not source.is_absolute() or candidate != source:
    raise SystemExit(1)
# A linked alias can redirect to another journal between admission and
# bootstrap. Inspect every path component, including external volumes, not
# only the final state directory; nonlinked exact external paths are valid.
at=source
while True:
    if at.is_symlink():
        raise SystemExit(1)
    if at==at.parent:
        break
    at=at.parent
print('1')
PYSTATE
)" || fail "v5 durable journal and workspace state identity differs; stage v6 with STATE_DIR=$LEGACY_STATE_DIR before cutover"
[ "$SAME_STATE" = 1 ] || fail "v5 durable journal and workspace state identity differs; stage v6 with STATE_DIR=$LEGACY_STATE_DIR before cutover"

R=(rclone --drive-root-folder-id "$ROOT_ID")
TMP_HEALTH="$(mktemp)"
"${R[@]}" copyto "${REMOTE}health.json" "$TMP_HEALTH" >/dev/null 2>&1 || fail "cannot read current production health.json"
python3 - "$TMP_HEALTH" "$ROOT_ID" "$TARGET_INSTANCE" "$CUTOVER_SOURCE_VERSION" <<'PYHEALTH'
import datetime,json,sys,time
h=json.load(open(sys.argv[1],encoding='utf-8'))
if h.get('kind')!='shell_bridge_health':
    raise SystemExit('production health record is not a bridge heartbeat')
if h.get('drive_root_folder_id')!=sys.argv[2] or h.get('bridge_instance_id')!=sys.argv[3]:
    raise SystemExit('production heartbeat does not match the pinned legacy mailbox/instance')
version=h.get('bridge_version')
if isinstance(version,bool) or str(version)!=sys.argv[4]:
    if sys.argv[4]=='5':
        raise SystemExit('production mailbox is not owned by v5; reconcile before cutting over')
    raise SystemExit('production mailbox is not owned by v6; reconcile before cutting over')
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

# An unreadable mailbox is not an empty mailbox. Never cut over on an
# rclone failure, including a broken pipe from truncating its output.
PENDING="$("${R[@]}" lsf "${REMOTE}requests" --files-only)" || fail "cannot list production request mailbox before cutover"
PENDING="$(printf '%s\n' "$PENDING" | sed '/^[[:space:]]*$/d')"
[ -z "$PENDING" ] || fail "production request mailbox is not empty; finish or reconcile pending requests first"

if [ "$CUTOVER_SOURCE_VERSION" = 6 ]; then
  PYTHONDONTWRITEBYTECODE=1 python3 "$(cd "$(dirname "$0")" && pwd)/verify_pending_approvals.py" "$LEGACY_STATE_DIR" ||
    fail "v6 source approval queue cannot be certified empty before shutdown"
fi

# Historical FINISHED files can still be indeterminate. A clean heartbeat
# STARTED-without-FINISHED counter is insufficient for a safe handover.
python3 "$(cd "$(dirname "$0")" && pwd)/verify_quiescent_journal.py" "$LEGACY_STATE_DIR" ||
  fail "legacy durable journal contains unresolved or indeterminate effects"

if [ "$CUTOVER_SOURCE_VERSION" = 6 ]; then
  # Existing v6 state includes both historical request receipts and workspace
  # continuity. Never silently adopt a second journal for a source upgrade.
  python3 - "$STATE_DIR" "$LEGACY_STATE_DIR" <<'PYUPGRADESTATE' || fail "v6-to-v6 candidate does not preserve the live durable state path"
from pathlib import Path
import sys
a,b=(Path(v).expanduser().resolve(strict=False) for v in sys.argv[1:])
raise SystemExit(0 if a == b else 1)
PYUPGRADESTATE

  # The original v5 cutover script predates v6 production. An explicitly
  # selected v6 source must also pass the separate PID-bound source gate.
  # This is pre-stop evidence ONLY, never post-stop/takeover authority.
  SOURCE_GATE="$(cd "$(dirname "$0")" && pwd)/v6_to_v6_preflight.py"
  [ -f "$SOURCE_GATE" ] && [ ! -L "$SOURCE_GATE" ] ||
    fail "v6-to-v6 pre-stop source gate is unavailable"
  PYTHONDONTWRITEBYTECODE=1 python3 - "$SOURCE_GATE" "$OWNERSHIP"       "$NEW_LABEL" "$NEW_CONFIG" "$TMP_HEALTH" "$STAGED_SOURCE_COMMIT" "$ROOT_ID" <<'PYV6GATE' || fail "v6-to-v6 source pre-stop identity and quiescence gate did not pass"
import importlib.util,json,sys
path,raw,new,config_path,health_path,commit,root=sys.argv[1:]
spec=importlib.util.spec_from_file_location("v6_to_v6_preflight",path)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
inventory=json.loads(raw)
owners=[r for r in inventory.get("loaded",[])
        if r.get("drive_root_folder_id")==root]
if len(owners)!=1:
    raise SystemExit("HOLD: v6 source must be one loaded mailbox owner")
source=owners[0]
candidate=json.load(open(config_path,encoding="utf-8"))
health=json.load(open(health_path,encoding="utf-8"))
source_cfg={k:source.get(k) for k in module._ID_FIELDS}
report=module.assess(
    inventory,source.get("label"),new,source_cfg,candidate,health,
    journal_verified=True,pending_mailbox_files=[],
    candidate_source_commit=commit,candidate_manifest_verified=True)
if not report["prestop_evidence_consistent"]:
    raise SystemExit("HOLD: v6 pre-stop gates: "+",".join(report["blockers"]))
if report["activation_authorized"] is not False:
    raise SystemExit("HOLD: pre-stop gate attempted to authorize cutover")
PYV6GATE
fi

if [ "$CUTOVER_SOURCE_VERSION" = 6 ]; then
  [ -f "$V6_REGISTRAR" ] && [ ! -L "$V6_REGISTRAR" ] ||
    fail "v6 login restart registration helper is unavailable"
  LOGIN_GATE="$(cd "$(dirname "$0")" && pwd)/verify_v6_login_claimants.py"
  [ -f "$LOGIN_GATE" ] && [ ! -L "$LOGIN_GATE" ] ||
    fail "read-only future-login owner inspector is unavailable"
  [ "${#OLD_LABELS[@]}" -eq 1 ] ||
    fail "v6-to-v6 handover requires exactly one source registration"
  V6_OLD_LABEL="${OLD_LABELS[0]}"
  V6_OLD_SOURCE_PLIST="$HOME/Library/LaunchAgents/$V6_OLD_LABEL.plist"
  V6_ARCHIVE_DIR="$HOME/Library/LaunchAgents/retired-v6-${EXPECTED_SOURCE_COMMIT:0:12}"
  V6_PERSIST_PLIST="$HOME/Library/LaunchAgents/$NEW_LABEL.plist"
  # The candidate must remain outside auto-loaded LaunchAgents until it
  # proves exact-build/PID and is the only possible mailbox consumer.
  case "$NEW_PLIST" in "$HOME/Library/LaunchAgents/"*) fail "v6 candidate plist must be staged inertly" ;; esac
  [ ! -e "$V6_PERSIST_PLIST" ] && [ ! -L "$V6_PERSIST_PLIST" ] ||
    fail "v6 replacement already has a persistent registration"
  PYTHONDONTWRITEBYTECODE=1 python3 "$LOGIN_GATE" "$HOME/Library/LaunchAgents" "$V6_OLD_LABEL" "$ROOT_ID" >/dev/null ||
    fail "source is not the only potential login owner of production mailbox"
fi

ROLLBACK_FILE="$(mktemp)"
for label in "${OLD_LABELS[@]}"; do
  plist="$HOME/Library/LaunchAgents/$label.plist"
  if launchctl print "gui/${UID_NOW}/${label}" >/dev/null 2>&1; then
    [ -f "$plist" ] || fail "loaded legacy service has no rollback plist: $label ($plist)"
    printf '%s|%s\n' "$label" "$plist" >> "$ROLLBACK_FILE"
  fi
done

# A future login during the handover must not wake the old mailbox owner.
# Reversibly archive its startup plist BEFORE stopping the process. Any
# ambiguous failed transition preserves a HOLD, not two login registrations.
if [ "$CUTOVER_SOURCE_VERSION" = 6 ]; then
  OLD_CONSUMERS_STOPPED=1
  V6_ARCHIVE_STARTED=1
  python3 "$V6_REGISTRAR" archive "$V6_OLD_SOURCE_PLIST" "$V6_ARCHIVE_DIR" "$V6_OLD_LABEL" ||
    fail "could not archive v6 source restart registration safely"
fi

# Stop old consumers first. Only then may v6 attach to the same production mailbox.
for label in "${OLD_LABELS[@]}"; do
  if launchctl print "gui/${UID_NOW}/${label}" >/dev/null 2>&1; then
    # A failed bootout may still have stopped the service. Record rollback
    # responsibility before attempting the non-atomic external operation.
    OLD_CONSUMERS_STOPPED=1
    launchctl bootout "gui/${UID_NOW}/${label}" || fail "failed to stop legacy service: $label"
  fi
done
for label in "${OLD_LABELS[@]}"; do
  if launchctl print "gui/${UID_NOW}/${label}" >/dev/null 2>&1; then
    fail "legacy service is still loaded after bootout: $label"
  fi
done

# Re-read loaded ownership after stopping the source. A new/parked
# consumer which wakes during the stop window must not be joined by the new
# v6 instance, and must not trigger automatic old-v6 restoration.
if [ "$CUTOVER_SOURCE_VERSION" = 6 ]; then
  V6_UNSAFE_TO_RESTORE=1
  POST_STOP_OWNERSHIP="$(PYTHONDONTWRITEBYTECODE=1 python3 "$OWNER_SCRIPT")" ||
    fail "v6 ownership changed or is unverifiable after source shutdown"
  python3 - "$POST_STOP_OWNERSHIP" "$ROOT_ID" <<'PYPOSTOWN'
import json,sys
inventory=json.loads(sys.argv[1]); root=sys.argv[2]
if inventory.get('safe_to_stage') is not True or inventory.get('problems'):
    raise SystemExit('HOLD: ambiguous loaded bridge ownership after stop')
if any(r.get('drive_root_folder_id')==root for r in inventory.get('loaded',[])):
    raise SystemExit('HOLD: another loaded bridge claimed production during handover')
PYPOSTOWN
  V6_UNSAFE_TO_RESTORE=0
fi

# Close the drain race: after legacy consumers are stopped, refuse to attach v6
# if a request arrived while the stop sequence was in progress. Rollback will
# restore any legacy consumers that were actually stopped.
PENDING_AFTER_STOP="$("${R[@]}" lsf "${REMOTE}requests" --files-only)" || fail "cannot list production request mailbox after stopping legacy consumers"
PENDING_AFTER_STOP="$(printf '%s\n' "$PENDING_AFTER_STOP" | sed '/^[[:space:]]*$/d')"
[ -z "$PENDING_AFTER_STOP" ] || fail "a request arrived during cutover drain; restoring legacy consumers before retry"

# Stop/drain may have changed approval or durable request state.
# A pending approval cannot be assumed absent merely because heartbeat
# reported zero active requests before old launchd shutdown.
if [ "$CUTOVER_SOURCE_VERSION" = 6 ]; then
  V6_UNSAFE_TO_RESTORE=1
  PYTHONDONTWRITEBYTECODE=1 python3 "$(cd "$(dirname "$0")" && pwd)/verify_pending_approvals.py" "$LEGACY_STATE_DIR" ||
    fail "v6 approval queue changed during the source handover"
fi

# Stop/drain may have altered durable state since the first snapshot.
# Re-read local FINISHED/STARTED markers after the old owner has stopped.
# An indeterminate state discovered after shutdown is not a recoverable
# v6-to-v6 pre-admission rollback; restarting the old v6 could replay it.
if [ "$CUTOVER_SOURCE_VERSION" = 6 ]; then V6_UNSAFE_TO_RESTORE=1; fi
python3 "$(cd "$(dirname "$0")" && pwd)/verify_quiescent_journal.py" "$LEGACY_STATE_DIR" ||
  fail "journal changed to unresolved/indeterminate during shutdown; hold all consumers"
if [ "$CUTOVER_SOURCE_VERSION" = 6 ]; then V6_UNSAFE_TO_RESTORE=0; fi

# From here a partially successful bootstrap may already have started v6,
# even when launchctl reports failure. Fail closed and require journal review.
STARTED_NEW=1
launchctl bootstrap "gui/${UID_NOW}" "$NEW_PLIST"
launchctl kickstart -k "gui/${UID_NOW}/${NEW_LABEL}"
launchctl print "gui/${UID_NOW}/${NEW_LABEL}" >/dev/null || fail "v6 LaunchAgent did not start"
python3 "$NEW_INSTALL_DIR/bridge.py" doctor --config "$NEW_CONFIG" >/dev/null || fail "v6 doctor failed after service switch"

# A correct installed manifest is not evidence that launchd actually started
# the intended binary. Require an independently published, fresh health record
# bound to the exact expected Git revision, target mailbox, and daemon PID.
HEALTH_VERIFIER="$(cd "$(dirname "$0")" && pwd)/verify_v6_candidate_health.py"
[ -f "$HEALTH_VERIFIER" ] || fail "exact-build health verifier missing"
LIVE_HEALTH="$(mktemp)"
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if "${R[@]}" copyto "${REMOTE}health.json" "$LIVE_HEALTH" >/dev/null 2>&1 &&
    python3 "$HEALTH_VERIFIER" "$NEW_CONFIG" "$LIVE_HEALTH" "$EXPECTED_SOURCE_COMMIT" "$NEW_LABEL" >/dev/null 2>&1; then
    LIVE_HEALTH_CONFIRMED=1
    break
  fi
  sleep 2
done
if [ "${LIVE_HEALTH_CONFIRMED:-0}" != 1 ]; then
  rm -f "$LIVE_HEALTH"
  fail "production daemon exact build, PID, or heartbeat not verified; rollback remains guarded"
fi
if [ "$CUTOVER_SOURCE_VERSION" = 6 ]; then
  # Only after exact PID/build identity is confirmed, publish the already
  # staged candidate plist as the single future-login production registration.
  # Mark intent first because file creation and acknowledgement can diverge.
  V6_TARGET_PERSISTED=1
  python3 "$V6_REGISTRAR" publish "$NEW_PLIST" "$V6_PERSIST_PLIST" "$NEW_LABEL" ||
    fail "candidate login restart registration could not be verified"
  # A fresh PID-bound health record is still not evidence that no second
  # LaunchAgent joined the mailbox during startup. Re-snapshot launchd after
  # successful candidate health verification and before the smoke request.
  V6_ACTIVATED_OWNERSHIP="$(PYTHONDONTWRITEBYTECODE=1 python3 "$OWNER_SCRIPT")" ||
    fail "v6 activation ownership is unverifiable"
  python3 - "$V6_ACTIVATED_OWNERSHIP" "$ROOT_ID" "$NEW_LABEL" <<'PYFINALOWN' || fail "v6 replacement is not the exclusive loaded mailbox owner"
import json,sys
inventory=json.loads(sys.argv[1]); root,label=sys.argv[2:]
if inventory.get('safe_to_stage') is not True or inventory.get('problems'):
    raise SystemExit('HOLD: loaded bridge collisions after replacement startup')
owners=[r for r in inventory.get('loaded',[])
        if r.get('drive_root_folder_id')==root]
if len(owners)!=1 or owners[0].get('label')!=label or not owners[0].get('running'):
    raise SystemExit('HOLD: expected exactly one running target service')
PYFINALOWN
fi
rm -f "$LIVE_HEALTH"

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
RESULT_LISTING="$("${R[@]}" --timeout 20s lsjson "${REMOTE}results" --files-only --max-depth 1 --include "$RID.json")" ||
  fail "production smoke result listing could not be independently verified"
printf '%s' "$RESULT_LISTING" |
  python3 "$(cd "$(dirname "$0")" && pwd)/verify_unique_result.py" "$RID" >/dev/null ||
  fail "production smoke result is missing or duplicated in Drive"
python3 - "$TMP_REQ" "$TMP_RESULT" "$RID" <<'PYRESULT'
import hashlib,json,sys
raw=open(sys.argv[1],'rb').read()
r=json.load(open(sys.argv[2],encoding='utf-8'))
rid=sys.argv[3]
expect={
    'protocol':1, 'kind':'result', 'id':rid, 'status':'completed',
    'exit_code':0, 'request_sha256':hashlib.sha256(raw).hexdigest(),
    'filesystem_sandboxed':True, 'network_sandboxed':True,
}
if any(r.get(k)!=v or (type(v) is int and type(r.get(k)) is not int)
       for k,v in expect.items()):
    raise SystemExit('v6 production smoke failed: identity, status, or sandbox')
if r.get('write_scope',{}).get('effective')!='read_only':
    raise SystemExit('v6 production smoke failed: scope')
if 'LOCAL_EXECUTOR_BRIDGE_OK' not in r.get('stdout_text',''):
    raise SystemExit('v6 production smoke failed: marker missing')
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
if [ "$CUTOVER_SOURCE_VERSION" = 6 ]; then
  printf 'old_label=%s\nold_archived=%s\nnew_label=%s\nnew_persisted=%s\nreboot_verified=0\n' \
    "$V6_OLD_LABEL" "$V6_ARCHIVE_DIR/$V6_OLD_LABEL.plist" "$NEW_LABEL" "$V6_PERSIST_PLIST" \
    > "$STATE_DIR/v6-upgrade-restart-registration.txt"
  chmod 600 "$STATE_DIR/v6-upgrade-restart-registration.txt"
fi
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
