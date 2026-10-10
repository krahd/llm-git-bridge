#!/bin/bash
set -euo pipefail

APP_NAME="local-executor-bridge"
LABEL="${LOCAL_EXECUTOR_LABEL:-net.laurenzo.local-executor-bridge}"
OLD_LABEL_1="com.tom.chatgpt-shell-bridge"
OLD_LABEL_2="io.llm-git-bridge.daemon"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/share/$APP_NAME}"
CONFIG_DIR="${CONFIG_DIR:-$HOME/.config/$APP_NAME}"
STATE_DIR="${STATE_DIR:-$HOME/.local/state/$APP_NAME}"
PLIST="${PLIST:-$HOME/Library/LaunchAgents/$LABEL.plist}"
ALLOWED_ROOT="${ALLOWED_ROOT:-}"
BASE_PATH="${BASE_PATH:-Local Executor Bridge}"
SHELL_BIN="${SHELL_BIN:-/bin/zsh}"
SMOKE_ATTEMPTS="${SMOKE_ATTEMPTS:-30}"
SMOKE_SLEEP="${SMOKE_SLEEP:-2}"
UID_NOW="$(id -u)"
STARTED_AGENT=0
STAGE_ONLY=0
RETIRE_OLD_AFTER_SMOKE=0
TMP_REQ=""
RESULT_TMP=""
TMP_MARKER=""

usage(){ echo "Usage: install.sh [--stage-only] [--retire-old-after-smoke]"; }
while [ "$#" -gt 0 ]; do
  case "$1" in
    --stage-only) STAGE_ONLY=1 ;;
    --retire-old-after-smoke) RETIRE_OLD_AFTER_SMOKE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

need(){ command -v "$1" >/dev/null 2>&1 || { echo "ERROR: missing required command: $1" >&2; exit 1; }; }
cleanup(){ rc=$?; [ -n "$TMP_REQ" ] && rm -f "$TMP_REQ" || true; [ -n "$RESULT_TMP" ] && rm -f "$RESULT_TMP" || true; [ -n "$TMP_MARKER" ] && rm -f "$TMP_MARKER" || true; if [ "$rc" -ne 0 ] && [ "$STARTED_AGENT" -eq 1 ]; then launchctl bootout "gui/${UID_NOW}" "$PLIST" >/dev/null 2>&1 || true; fi; }
trap cleanup EXIT

[ "$(uname -s)" = Darwin ] || { echo "ERROR: this installer is for macOS" >&2; exit 1; }
need python3; need rclone; need git; need launchctl
[ -x "$SHELL_BIN" ] || { echo "ERROR: shell is not executable: $SHELL_BIN" >&2; exit 1; }

fail(){ echo "ERROR: $*" >&2; exit 1; }
normalize_remote(){ case "$1" in *:) printf '%s\n' "$1";; *) printf '%s:\n' "$1";; esac; }
marker_for(){ rclone cat "$1$2/bridge-instance.json" 2>/dev/null || true; }
valid_marker(){ python3 - "$1" <<'PY'
import json,sys
try: o=json.loads(sys.argv[1])
except Exception: raise SystemExit(1)
raise SystemExit(0 if o.get('protocol')==1 and o.get('kind')=='shell_bridge_instance' and isinstance(o.get('bridge_instance_id'),str) and o.get('bridge_instance_id') else 1)
PY
}

choose_allowed_root() {
  if [ -z "$ALLOWED_ROOT" ]; then
    [ -t 0 ] || fail "ALLOWED_ROOT is required for non-interactive installation"
    printf 'Local directory ChatGPT may access (for example %s/repos): ' "$HOME"
    IFS= read -r ALLOWED_ROOT || ALLOWED_ROOT=''
    [ -n "$ALLOWED_ROOT" ] || fail "an allowed root is required"
  fi
  case "$ALLOWED_ROOT" in
    '~') ALLOWED_ROOT="$HOME" ;;
    '~/'*) ALLOWED_ROOT="$HOME/${ALLOWED_ROOT#~/}" ;;
  esac
  [ -d "$ALLOWED_ROOT" ] || fail "allowed root does not exist: $ALLOWED_ROOT"
  ALLOWED_ROOT="$(cd "$ALLOWED_ROOT" && pwd -P)"
}

choose_remote() {
  if [ -n "${RCLONE_REMOTE:-}" ]; then
    REMOTE="$(normalize_remote "$RCLONE_REMOTE")"
    rclone listremotes | grep -Fxq "$REMOTE" || fail "configured RCLONE_REMOTE does not exist: $REMOTE"
    return
  fi

  local remotes=() matches=() remote marker selection
  while IFS= read -r remote; do
    [ -n "$remote" ] || continue
    remotes+=("$remote")
    marker="$(marker_for "$remote" "$BASE_PATH")"
    if [ -n "$marker" ] && valid_marker "$marker"; then
      matches+=("$remote")
    fi
  done < <(rclone listremotes)

  if [ "${#matches[@]}" -eq 1 ]; then
    REMOTE="${matches[0]}"
    return
  fi
  [ "${#matches[@]}" -eq 0 ] || fail "multiple rclone remotes contain a valid $BASE_PATH mailbox; set RCLONE_REMOTE explicitly"
  [ "${#remotes[@]}" -gt 0 ] || fail "no rclone remotes are configured; run 'rclone config' first"
  if [ "${#remotes[@]}" -eq 1 ]; then
    REMOTE="${remotes[0]}"
    return
  fi

  [ -t 0 ] || fail "multiple rclone remotes are configured; set RCLONE_REMOTE for non-interactive installation"
  echo "Choose the rclone remote that should carry the Shell Bridge mailbox:"
  local i=1
  for remote in "${remotes[@]}"; do printf '  %d) %s\n' "$i" "$remote"; i=$((i+1)); done
  printf 'Remote number: '
  IFS= read -r selection || selection=''
  case "$selection" in *[!0-9]*|'') fail "invalid remote selection" ;; esac
  [ "$selection" -ge 1 ] && [ "$selection" -le "${#remotes[@]}" ] || fail "invalid remote selection"
  REMOTE="${remotes[$((selection-1))]}"
}

ensure_mailbox() {
  local root_json count marker instance
  root_json="$(rclone lsjson "$REMOTE" --dirs-only --max-depth 1)"
  count="$(python3 - "$BASE_PATH" "$root_json" <<'PYCOUNT'
import json,sys
name,raw=sys.argv[1:]
print(sum(1 for x in json.loads(raw) if x.get('IsDir') and x.get('Name')==name))
PYCOUNT
)"
  [ "$count" -le 1 ] || fail "multiple Drive folders are named exactly '$BASE_PATH'; keep one live mailbox and archive the others"
  if [ "$count" -eq 0 ]; then
    echo "Creating Drive mailbox: $BASE_PATH"
    rclone mkdir "${REMOTE}${BASE_PATH}"
    instance="shell-bridge-$(python3 - <<'PYID'
import uuid
print(uuid.uuid4().hex)
PYID
)"
    TMP_MARKER="$(mktemp)"
    python3 - "$TMP_MARKER" "$instance" <<'PYMARKER'
import json,sys
path,instance=sys.argv[1:]
with open(path,'w',encoding='utf-8') as f:
    json.dump({'protocol':1,'kind':'shell_bridge_instance','bridge_instance_id':instance},f,separators=(',',':'),sort_keys=True)
    f.write('\n')
PYMARKER
    rclone copyto "$TMP_MARKER" "${REMOTE}${BASE_PATH}/bridge-instance.json"
  else
    marker="$(marker_for "$REMOTE" "$BASE_PATH")"
    valid_marker "$marker" || fail "'$BASE_PATH' already exists but has no valid bridge-instance.json; refusing to adopt it implicitly"
  fi
}

choose_allowed_root
choose_remote
ensure_mailbox

# Inspect only rclone's redacted view; never print OAuth material.
if ! rclone config redacted "${REMOTE%:}" 2>/dev/null | grep -Eq '^[[:space:]]*client_id[[:space:]]*='; then
  echo "WARNING: Drive remote $REMOTE has no private OAuth client_id; rclone's shared client is being retired during 2026." >&2
fi

ROOT_JSON="$(rclone lsjson "$REMOTE" --dirs-only --max-depth 1)"
ROOT_ID="$(python3 - "$BASE_PATH" "$ROOT_JSON" <<'PY'
import json,sys
name,raw=sys.argv[1:]
items=[x for x in json.loads(raw) if x.get('IsDir') and x.get('Name')==name and x.get('ID')]
if len(items)!=1: raise SystemExit(2)
print(items[0]['ID'])
PY
)" || { echo "ERROR: could not resolve exactly one Drive folder ID for $BASE_PATH" >&2; exit 1; }
MARKER="$(marker_for "$REMOTE" "$BASE_PATH")"
valid_marker "$MARKER" || { echo "ERROR: invalid bridge instance marker" >&2; exit 1; }
INSTANCE_ID="$(python3 - "$MARKER" <<'PY'
import json,sys; print(json.loads(sys.argv[1])['bridge_instance_id'])
PY
)"

R=(rclone --drive-root-folder-id "$ROOT_ID")
"${R[@]}" mkdir "${REMOTE}requests"
"${R[@]}" mkdir "${REMOTE}results"
DIRS_JSON="$("${R[@]}" lsjson "$REMOTE" --dirs-only --max-depth 1)"
read -r REQUESTS_ID RESULTS_ID < <(python3 - "$DIRS_JSON" <<'PY'
import json,sys
m={x.get('Name'):x.get('ID') for x in json.loads(sys.argv[1]) if x.get('IsDir')}
print(m.get('requests',''),m.get('results',''))
PY
)
[ -n "$REQUESTS_ID" ] && [ -n "$RESULTS_ID" ] || { echo "ERROR: request/result folder IDs unavailable" >&2; exit 1; }

echo "Using verified Drive remote: $REMOTE"
echo "Pinned Drive root ID: $ROOT_ID"
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" "$STATE_DIR" "$HOME/Library/LaunchAgents"
chmod 700 "$INSTALL_DIR" "$CONFIG_DIR" "$STATE_DIR"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cp "$SCRIPT_DIR/bridge.py" "$INSTALL_DIR/bridge.py"
cp "$SCRIPT_DIR/workspace.py" "$INSTALL_DIR/workspace.py"
cp "$SCRIPT_DIR/approval_helper.py" "$INSTALL_DIR/approval_helper.py"
cp "$SCRIPT_DIR/trusted_operations.py" "$INSTALL_DIR/trusted_operations.py"
cp "$SCRIPT_DIR/bridge_mailbox_inspector.py" "$INSTALL_DIR/bridge_mailbox_inspector.py"
cp "$SCRIPT_DIR/ssh_pinned_helper.py" "$INSTALL_DIR/ssh_pinned_helper.py"
chmod 700 "$INSTALL_DIR/bridge.py" "$INSTALL_DIR/workspace.py" "$INSTALL_DIR/approval_helper.py" "$INSTALL_DIR/trusted_operations.py" "$INSTALL_DIR/bridge_mailbox_inspector.py" "$INSTALL_DIR/ssh_pinned_helper.py"
SOURCE_COMMIT="$(git -C "$SCRIPT_DIR/.." rev-parse HEAD)"
python3 - "$INSTALL_DIR/install-manifest.json" "$SOURCE_COMMIT" "$INSTALL_DIR/bridge.py" "$INSTALL_DIR/workspace.py" "$INSTALL_DIR/approval_helper.py" "$INSTALL_DIR/trusted_operations.py" "$INSTALL_DIR/bridge_mailbox_inspector.py" <<'PYMAN'
import hashlib,json,sys
manifest_path,source_commit,bridge_path,workspace_path,approval_helper_path,trusted_path,inspector_path=sys.argv[1:]
def sha256(path):
    with open(path,'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()
manifest={
    'schema':1,
    'source_commit':source_commit,
    'bridge_sha256':sha256(bridge_path),
    'workspace_sha256':sha256(workspace_path),
    'approval_helper_sha256':sha256(approval_helper_path),
    'trusted_operations_sha256':sha256(trusted_path),
    'bridge_mailbox_inspector_sha256':sha256(inspector_path),
}
with open(manifest_path,'w',encoding='utf-8') as f:
    json.dump(manifest,f,indent=2,sort_keys=True)
    f.write('\n')
PYMAN
chmod 600 "$INSTALL_DIR/install-manifest.json"

python3 - "$CONFIG_DIR/config.json" "$REMOTE" "$BASE_PATH" "$ROOT_ID" "$REQUESTS_ID" "$RESULTS_ID" "$INSTANCE_ID" "$ALLOWED_ROOT" "$STATE_DIR" "$SHELL_BIN" "$INSTALL_DIR" <<'PY'
import hashlib,json,os,sys
from pathlib import Path
path,remote,base,root_id,req_id,res_id,instance,allowed,state,shell,install_dir=sys.argv[1:]
try:
    old=json.load(open(path,encoding='utf-8'))
except Exception:
    old={}
cfg=dict(old)
cfg.pop('operator_approval_public_key', None)
cfg.pop('operator_approval_public_key_sha256', None)
cfg.update({
 'remote':remote,'base_path':base,'drive_root_folder_id':root_id,
 'requests_folder_id':req_id,'results_folder_id':res_id,'bridge_instance_id':instance,
 'allowed_root':allowed,'state_dir':state,'shell':shell,
 'max_timeout_seconds':300,'rclone_timeout_seconds':30,'poll_seconds':2.0,
 'max_active_requests':cfg.get('max_active_requests','auto'),'health_seconds':60.0,
 'max_request_bytes':2*1024*1024,'max_command_bytes':256*1024,
 'max_stdin_bytes':1024*1024,'max_output_bytes':16*1024*1024,
 'operator_confirmation_mode':cfg.get('operator_confirmation_mode','auto'),
 'operator_confirmation_timeout_seconds':cfg.get('operator_confirmation_timeout_seconds',300),
 'operator_approval_app': str(Path(install_dir)/'Local Executor Approval.app'),
 'wake_lease_enabled':cfg.get('wake_lease_enabled',True),
 'wake_grace_seconds':cfg.get('wake_grace_seconds',3600.0),
})
# Only an out-of-band installer may opt into the fixed, read-only
# inspector. Its executable is outside the agent-writable repository roots,
# pinned to a SHA-256, and every invocation still requires operator approval.
if os.environ.get("REGISTER_MAILBOX_INSPECTOR") == "1":
    executable=Path(install_dir)/"bridge_mailbox_inspector.py"
    root=Path(allowed).resolve()
    try:
        executable.resolve().relative_to(root)
    except ValueError:
        pass
    else:
        raise SystemExit("refusing agent-writable inspector registration")
    if executable.is_symlink() or not executable.is_file():
        raise SystemExit("inspector install is missing or symlinked")
    ops=cfg.get("trusted_operations",{})
    if not isinstance(ops,dict) or "bridge-mailbox-inspect" in ops:
        raise SystemExit("existing or malformed trusted helper registry; manual reconciliation required")
    ops=dict(ops)
    ops["bridge-mailbox-inspect"]={
        "executable":str(executable),
        "executable_sha256":hashlib.sha256(executable.read_bytes()).hexdigest(),
        "permitted_roots":[str(Path.home())],
        "permitted_actions":["inspect"],
    }
    cfg["trusted_operations"]=ops
# An opt-in operator-owned, host-pinned SSH policy may register a fixed,
# no-arbitrary-command remote status helper. No host or command is chosen by
# the agent's request.
ssh_dir=os.environ.get("REGISTER_PINNED_SSH_POLICY_DIR")
if ssh_dir:
    import importlib.util
    directory=Path(ssh_dir)
    executable=Path(install_dir)/"ssh_pinned_helper.py"
    if not directory.is_absolute() or directory.is_symlink():
        raise SystemExit("SSH policy directory must be absolute and non-symlinked")
    root=Path(allowed).resolve()
    for item in (directory.resolve(),executable.resolve()):
        try:
            item.relative_to(root)
        except ValueError:
            pass
        else:
            raise SystemExit("refusing agent-writable SSH capability")
    if executable.is_symlink() or not executable.is_file():
        raise SystemExit("pinned SSH helper unavailable")
    module=importlib.util.spec_from_file_location("pinned_ssh_helper",str(executable))
    helper=importlib.util.module_from_spec(module)
    module.loader.exec_module(helper)
    for action in ("status","identity"):
        helper.load_policy(directory,action)
    ops=cfg.get("trusted_operations",{})
    if not isinstance(ops,dict) or "ssh-pinned-readonly" in ops:
        raise SystemExit("existing or malformed SSH trusted helper registration")
    ops=dict(ops)
    ops["ssh-pinned-readonly"]={
        "executable":str(executable),
        "executable_sha256":hashlib.sha256(executable.read_bytes()).hexdigest(),
        "permitted_roots":[str(directory)],
        "permitted_actions":["status","identity"],
    }
    cfg["trusted_operations"]=ops
with open(path,'w',encoding='utf-8') as f: json.dump(cfg,f,indent=2,sort_keys=True); f.write('\n')
PY
chmod 600 "$CONFIG_DIR/config.json"

RCLONE_BIN="$(command -v rclone)"; PYTHON_BIN="$(command -v python3)"
APP_BUNDLE="$INSTALL_DIR/Local Executor Approval.app"
DEVELOPER_DIR_VALUE="${DEVELOPER_DIR:-$(/usr/bin/xcode-select -p 2>/dev/null || true)}"
DIRECT_SWIFTC="$DEVELOPER_DIR_VALUE/Toolchains/XcodeDefault.xctoolchain/usr/bin/swiftc"
if [ -x "$DIRECT_SWIFTC" ]; then SWIFTC_BIN="$DIRECT_SWIFTC"; else SWIFTC_BIN="$(command -v swiftc)" || fail "swiftc is required to build the native approval menu-bar app"; fi
SWIFT_SDK="$DEVELOPER_DIR_VALUE/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"
[ -d "$SWIFT_SDK" ] || fail "macOS SDK is required to build the native approval menu-bar app: $SWIFT_SDK"
mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources"
install -m 644 "$SCRIPT_DIR/assets/bridge-menubar.png" "$APP_BUNDLE/Contents/Resources/bridge-menubar.png"
APPROVAL_ROOT="$STATE_DIR/approvals"
python3 - "$APP_BUNDLE/Contents/Info.plist" "$APPROVAL_ROOT" "$INSTALL_DIR/approval_helper.py" "$PYTHON_BIN" <<'PYAPPPLIST'
import plistlib,sys
path,approval_root,helper_path,python_path=sys.argv[1:]
obj={'CFBundleIdentifier':'net.laurenzo.local-executor-approval','CFBundleName':'Local Executor Approval','CFBundleDisplayName':'Local Executor Approval','CFBundlePackageType':'APPL','CFBundleExecutable':'local-executor-approval','CFBundleVersion':'1','CFBundleShortVersionString':'1.0','LSUIElement':True,'NSHighResolutionCapable':True,'ApprovalRoot':approval_root,'ApprovalHelperPath':helper_path,'ApprovalPythonPath':python_path}
with open(path,'wb') as f: plistlib.dump(obj,f,fmt=plistlib.FMT_XML,sort_keys=True)
PYAPPPLIST
"$SWIFTC_BIN" -sdk "$SWIFT_SDK" "$SCRIPT_DIR/approval_gui.swift" -framework AppKit -framework Foundation -o "$APP_BUNDLE/Contents/MacOS/local-executor-approval"
chmod 700 "$APP_BUNDLE/Contents/MacOS/local-executor-approval"


PATH_VALUE="$(dirname "$RCLONE_BIN"):$(dirname "$PYTHON_BIN"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
python3 - "$PLIST" "$LABEL" "$PYTHON_BIN" "$INSTALL_DIR/bridge.py" "$CONFIG_DIR/config.json" "$PATH_VALUE" "$STATE_DIR" "$HOME" <<'PY'
import plistlib,sys
plist,label,python,bridge,config,path_value,state,home=sys.argv[1:]
obj={'Label':label,'ProgramArguments':[python,bridge,'daemon','--config',config],
'EnvironmentVariables':{'PATH':path_value,'HOME':home,'LOCAL_EXECUTOR_BRIDGE_STATE_DIR':state,'CHATGPT_SHELL_BRIDGE_STATE_DIR':state},'RunAtLoad':True,'KeepAlive':True,'Umask':0o077,
'StandardOutPath':state+'/stdout.log','StandardErrorPath':state+'/stderr.log'}
with open(plist,'wb') as f: plistlib.dump(obj,f,fmt=plistlib.FMT_XML,sort_keys=True)
PY
chmod 600 "$PLIST"

python3 - "$INSTALL_DIR/install-manifest.json" "$CONFIG_DIR/config.json" "$PLIST" "$APP_BUNDLE/Contents/Info.plist" "$APP_BUNDLE/Contents/MacOS/local-executor-approval" <<'PYGENERATED'
import hashlib,json,os,sys,tempfile
manifest_path,config_path,plist_path,app_info_path,app_exec_path=sys.argv[1:]
def sha256(path):
    with open(path,'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()
with open(manifest_path,encoding='utf-8') as f:
    manifest=json.load(f)
manifest.update({
    'config_sha256':sha256(config_path),
    'launchagent_plist_sha256':sha256(plist_path),
    'approval_app_info_sha256':sha256(app_info_path),
    'approval_app_executable_sha256':sha256(app_exec_path),
})
parent=os.path.dirname(manifest_path)
fd,tmp=tempfile.mkstemp(prefix='.install-manifest.',dir=parent)
try:
    with os.fdopen(fd,'w',encoding='utf-8') as f:
        json.dump(manifest,f,indent=2,sort_keys=True); f.write(chr(10)); f.flush(); os.fsync(f.fileno())
    os.chmod(tmp,0o600)
    os.replace(tmp,manifest_path)
finally:
    if os.path.exists(tmp): os.unlink(tmp)
PYGENERATED

if [ "$STAGE_ONLY" -eq 1 ]; then
  echo "SHELL_BRIDGE_STAGED=1"
  echo "ROOT_ID:   $ROOT_ID"
  echo "REQUESTS:  $REQUESTS_ID"
  echo "RESULTS:   $RESULTS_ID"
  echo "CONFIG:    $CONFIG_DIR/config.json"
  echo "WORKSPACE: $INSTALL_DIR/workspace.py"
  echo "LABEL:     $LABEL"
  exit 0
fi

# Never start v6 beside an active v5 daemon when both point at the same mailbox.
# Same-mailbox upgrades must be staged, then switched out-of-band with cutover.sh.
LEGACY_SHELL_CONFIG="$HOME/.config/chatgpt-shell-bridge/config.json"
if [ -f "$LEGACY_SHELL_CONFIG" ] && launchctl print "gui/${UID_NOW}/${OLD_LABEL_1}" >/dev/null 2>&1; then
  LEGACY_ROOT_ID="$(python3 - "$LEGACY_SHELL_CONFIG" <<'PYLEGACY'
import json,sys
try: c=json.load(open(sys.argv[1],encoding='utf-8'))
except Exception: c={}
v=c.get('drive_root_folder_id','')
print(v if isinstance(v,str) else '')
PYLEGACY
)"
  if [ -n "$LEGACY_ROOT_ID" ] && [ "$LEGACY_ROOT_ID" = "$ROOT_ID" ]; then
    fail "active v5 uses this same mailbox; run install.sh --stage-only, then run cutover.sh out-of-band"
  fi
fi

# v6 never retires the old service before the new service passes its smoke test.
launchctl bootout "gui/${UID_NOW}" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/${UID_NOW}" "$PLIST"
STARTED_AGENT=1
launchctl kickstart -k "gui/${UID_NOW}/${LABEL}"
sleep 1
launchctl print "gui/${UID_NOW}/${LABEL}" >/dev/null 2>&1 || { echo "ERROR: LaunchAgent did not start" >&2; exit 1; }

python3 "$INSTALL_DIR/bridge.py" doctor --config "$CONFIG_DIR/config.json" >/dev/null || { echo "ERROR: doctor failed" >&2; exit 1; }
RID="install-smoke-$(date +%Y%m%d%H%M%S)-$$"
TMP_REQ="$(mktemp)"; RESULT_TMP="$(mktemp)"
python3 - "$TMP_REQ" "$RID" "$ALLOWED_ROOT" <<'PY'
import json,sys
path,rid,root=sys.argv[1:]
json.dump({'protocol':1,'id':rid,'cwd':root,'explanation':'Verify the newly installed Shell Bridge can execute a harmless repository smoke test.','command':"printf 'SHELL_BRIDGE_OK\\n'; git --version; pwd",'timeout_seconds':30},open(path,'w'),separators=(',',':'),sort_keys=True)
PY
"${R[@]}" copyto "$TMP_REQ" "${REMOTE}requests/${RID}.json"
FOUND=0
for ((i=0;i<SMOKE_ATTEMPTS;i++)); do
  if "${R[@]}" copyto "${REMOTE}results/${RID}.json" "$RESULT_TMP" >/dev/null 2>&1; then FOUND=1; break; fi
  sleep "$SMOKE_SLEEP"
done
[ "$FOUND" -eq 1 ] || { echo "ERROR: end-to-end smoke test produced no result" >&2; exit 1; }
python3 - "$RESULT_TMP" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))
if r.get('status')!='completed' or r.get('exit_code')!=0 or 'SHELL_BRIDGE_OK' not in r.get('stdout_text',''):
 print(json.dumps(r,indent=2)); raise SystemExit('ERROR: smoke test failed')
print('SHELL_BRIDGE_INSTALLED=1')
PY
"${R[@]}" deletefile "${REMOTE}results/${RID}.json" >/dev/null 2>&1 || true
STARTED_AGENT=0

if [ "$RETIRE_OLD_AFTER_SMOKE" -eq 1 ]; then
  for old_label in "$OLD_LABEL_1" "$OLD_LABEL_2"; do
    old_plist="$HOME/Library/LaunchAgents/$old_label.plist"
    launchctl bootout "gui/${UID_NOW}/${old_label}" >/dev/null 2>&1 || \
      launchctl bootout "gui/${UID_NOW}" "$old_plist" >/dev/null 2>&1 || true
    if [ -f "$old_plist" ]; then
      archive_dir="$STATE_DIR/retired-launchagents"
      mkdir -p "$archive_dir"; chmod 700 "$archive_dir"
      mv "$old_plist" "$archive_dir/${old_label}.$(date +%Y%m%d%H%M%S).plist"
    fi
  done
fi

echo "ROOT_ID:   $ROOT_ID"
echo "REQUESTS:  $REQUESTS_ID"
echo "RESULTS:   $RESULTS_ID"
echo "CONFIG:    $CONFIG_DIR/config.json"
echo "WORKSPACE: $INSTALL_DIR/workspace.py"
