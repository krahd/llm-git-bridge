#!/bin/bash
set -euo pipefail

APP_NAME="chatgpt-shell-bridge"
LABEL="${SHELL_BRIDGE_LABEL:-io.llm-git-bridge.daemon}"
LEGACY_LABEL="io.llm-git-bridge.chatgpt-shell-bridge"
LEGACY_PLIST="$HOME/Library/LaunchAgents/$LEGACY_LABEL.plist"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/share/$APP_NAME}"
CONFIG_DIR="${CONFIG_DIR:-$HOME/.config/$APP_NAME}"
STATE_DIR="${STATE_DIR:-$HOME/.local/state/$APP_NAME}"
PLIST="${PLIST:-$HOME/Library/LaunchAgents/$LABEL.plist}"
ALLOWED_ROOT="${ALLOWED_ROOT:-}"
BASE_PATH="${BASE_PATH:-ChatGPT Shell Bridge}"
SHELL_BIN="${SHELL_BIN:-/bin/zsh}"
SMOKE_ATTEMPTS="${SMOKE_ATTEMPTS:-30}"
SMOKE_SLEEP="${SMOKE_SLEEP:-2}"
UID_NOW="$(id -u)"
STARTED_AGENT=0
STAGE_ONLY=0
TMP_REQ=""
RESULT_TMP=""
TMP_MARKER=""

usage(){ echo "Usage: install.sh [--stage-only]"; }
while [ "$#" -gt 0 ]; do
  case "$1" in
    --stage-only) STAGE_ONLY=1 ;;
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
chmod 700 "$INSTALL_DIR/bridge.py" "$INSTALL_DIR/workspace.py"
SOURCE_COMMIT="$(git -C "$SCRIPT_DIR/.." rev-parse HEAD)"
python3 - "$INSTALL_DIR/install-manifest.json" "$SOURCE_COMMIT" "$INSTALL_DIR/bridge.py" "$INSTALL_DIR/workspace.py" <<'PYMAN'
import hashlib,json,sys
manifest_path,source_commit,bridge_path,workspace_path=sys.argv[1:]
def sha256(path):
    with open(path,'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()
manifest={
    'schema':1,
    'source_commit':source_commit,
    'bridge_sha256':sha256(bridge_path),
    'workspace_sha256':sha256(workspace_path),
}
with open(manifest_path,'w',encoding='utf-8') as f:
    json.dump(manifest,f,indent=2,sort_keys=True)
    f.write('\n')
PYMAN
chmod 600 "$INSTALL_DIR/install-manifest.json"

python3 - "$CONFIG_DIR/config.json" "$REMOTE" "$BASE_PATH" "$ROOT_ID" "$REQUESTS_ID" "$RESULTS_ID" "$INSTANCE_ID" "$ALLOWED_ROOT" "$STATE_DIR" "$SHELL_BIN" <<'PY'
import json,sys
path,remote,base,root_id,req_id,res_id,instance,allowed,state,shell=sys.argv[1:]
try:
    old=json.load(open(path,encoding='utf-8'))
except Exception:
    old={}
cfg=dict(old)
cfg.update({
 'remote':remote,'base_path':base,'drive_root_folder_id':root_id,
 'requests_folder_id':req_id,'results_folder_id':res_id,'bridge_instance_id':instance,
 'allowed_root':allowed,'state_dir':state,'shell':shell,
 'max_timeout_seconds':300,'rclone_timeout_seconds':30,'poll_seconds':2.0,
 'max_active_requests':cfg.get('max_active_requests','auto'),'health_seconds':60.0,
 'max_request_bytes':2*1024*1024,'max_command_bytes':256*1024,
 'max_stdin_bytes':1024*1024,'max_output_bytes':16*1024*1024,
})
with open(path,'w',encoding='utf-8') as f: json.dump(cfg,f,indent=2,sort_keys=True); f.write('\n')
PY
chmod 600 "$CONFIG_DIR/config.json"

RCLONE_BIN="$(command -v rclone)"; PYTHON_BIN="$(command -v python3)"
PATH_VALUE="$(dirname "$RCLONE_BIN"):$(dirname "$PYTHON_BIN"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
python3 - "$PLIST" "$LABEL" "$PYTHON_BIN" "$INSTALL_DIR/bridge.py" "$CONFIG_DIR/config.json" "$PATH_VALUE" "$STATE_DIR" "$HOME" <<'PY'
import plistlib,sys
plist,label,python,bridge,config,path_value,state,home=sys.argv[1:]
obj={'Label':label,'ProgramArguments':[python,bridge,'daemon','--config',config],
'EnvironmentVariables':{'PATH':path_value,'HOME':home},'RunAtLoad':True,'KeepAlive':True,'Umask':0o077,
'StandardOutPath':state+'/stdout.log','StandardErrorPath':state+'/stderr.log'}
with open(plist,'wb') as f: plistlib.dump(obj,f,fmt=plistlib.FMT_XML,sort_keys=True)
PY
chmod 600 "$PLIST"

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

# Retire the one known pre-canonical LaunchAgent. Archive its plist for recovery.
if [ "$LABEL" != "$LEGACY_LABEL" ]; then
  launchctl bootout "gui/${UID_NOW}/${LEGACY_LABEL}" >/dev/null 2>&1 || \
    launchctl bootout "gui/${UID_NOW}" "$LEGACY_PLIST" >/dev/null 2>&1 || true
  if [ -f "$LEGACY_PLIST" ]; then
    LEGACY_ARCHIVE_DIR="$STATE_DIR/legacy-launchagent"
    mkdir -p "$LEGACY_ARCHIVE_DIR"
    chmod 700 "$LEGACY_ARCHIVE_DIR"
    LEGACY_STAMP="$(date +%Y%m%d%H%M%S)"
    mv "$LEGACY_PLIST" "$LEGACY_ARCHIVE_DIR/${LEGACY_LABEL}.${LEGACY_STAMP}.plist"
  fi
fi

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
json.dump({'protocol':1,'id':rid,'cwd':root,'command':"printf 'SHELL_BRIDGE_OK\\n'; git --version; pwd",'timeout_seconds':30},open(path,'w'),separators=(',',':'),sort_keys=True)
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

echo "ROOT_ID:   $ROOT_ID"
echo "REQUESTS:  $REQUESTS_ID"
echo "RESULTS:   $RESULTS_ID"
echo "CONFIG:    $CONFIG_DIR/config.json"
echo "WORKSPACE: $INSTALL_DIR/workspace.py"
