#!/bin/bash
set -euo pipefail

LABEL="net.laurenzo.chatgpt-conversation-harness-v1"
APP_ID="chatgpt-conversation-harness-v1"
INSTALL_DIR="${HARNESS_INSTALL_DIR:-$HOME/.local/share/$APP_ID}"
STATE_DIR="${HARNESS_STATE_DIR:-$HOME/.local/state/$APP_ID}"
PLIST="${HARNESS_PLIST:-$HOME/Library/LaunchAgents/$LABEL.plist}"
ACTIVATE=0

usage() {
  cat <<EOF
Usage: install.sh [--stage-only | --activate]

--stage-only  Install/update files and plist without changing launchd (default).
--activate    Install/update files and start/restart only the harness LaunchAgent.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --stage-only) ACTIVATE=0 ;;
    --activate) ACTIVATE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

[ "$(uname -s)" = Darwin ] || { echo "ERROR: this installer is for macOS" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 is required" >&2; exit 1; }

case "$INSTALL_DIR:$STATE_DIR:$PLIST" in
  *chatgpt-shell-bridge*)
    echo "ERROR: harness paths must not overlap the ChatGPT Shell Bridge runtime" >&2
    exit 1
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$(command -v python3)"
PY_ROOT="$INSTALL_DIR/python"
BIN_DIR="$INSTALL_DIR/bin"
SAFARI_EXTENSION_DIR="$INSTALL_DIR/safari-extension"

mkdir -p "$PY_ROOT/llm_git_bridge" "$BIN_DIR" "$STATE_DIR" "$(dirname "$PLIST")"
chmod 700 "$INSTALL_DIR" "$PY_ROOT" "$BIN_DIR" "$STATE_DIR"
printf '%s\n' '"""Private package root for the standalone conversation harness runtime."""' > "$PY_ROOT/llm_git_bridge/__init__.py"
rm -rf "$PY_ROOT/llm_git_bridge/harness"
cp -R "$REPO_ROOT/src/llm_git_bridge/harness" "$PY_ROOT/llm_git_bridge/harness"
# Never ship bytecode copied from the development checkout. Compile a fresh cache
# after staging the source into the isolated runtime.
find "$PY_ROOT/llm_git_bridge/harness" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$PY_ROOT/llm_git_bridge/harness" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
find "$PY_ROOT/llm_git_bridge/harness" -type d -exec chmod 700 {} +
find "$PY_ROOT/llm_git_bridge/harness" -type f -exec chmod 600 {} +
python3 -m compileall -q "$PY_ROOT/llm_git_bridge/harness"

# Stage the standard WebExtension separately from the Python runtime. Safari 18.4+
# can load this directory temporarily for development without an Xcode project.
rm -rf "$SAFARI_EXTENSION_DIR"
cp -R "$REPO_ROOT/safari/extension" "$SAFARI_EXTENSION_DIR"
find "$SAFARI_EXTENSION_DIR" -type d -exec chmod 700 {} +
find "$SAFARI_EXTENSION_DIR" -type f -exec chmod 600 {} +
[ -f "$SAFARI_EXTENSION_DIR/manifest.json" ] || { echo "ERROR: staged Safari extension has no manifest.json" >&2; exit 1; }

cat > "$BIN_DIR/harness" <<EOF
#!/bin/sh
set -eu
export PYTHONPATH="$PY_ROOT"
exec "$PYTHON_BIN" -m llm_git_bridge.harness.cli "\$@"
EOF
chmod 700 "$BIN_DIR/harness"

SOURCE_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || printf unknown)"
python3 - "$INSTALL_DIR/install-manifest.json" "$SOURCE_COMMIT" "$REPO_ROOT/src/llm_git_bridge/harness" "$REPO_ROOT/safari/extension" <<'PY'
import hashlib,json,sys
from pathlib import Path
manifest_path,source_commit,source_dir,safari_dir=sys.argv[1:]
files={}
for path in sorted(Path(source_dir).glob('*.py')):
    files[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
safari_files={}
safari_root=Path(safari_dir)
for path in sorted(p for p in safari_root.rglob('*') if p.is_file()):
    safari_files[path.relative_to(safari_root).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
obj={'schema':2,'app_id':'chatgpt-conversation-harness-v1','source_commit':source_commit,'files':files,'safari_files':safari_files}
with open(manifest_path,'w',encoding='utf-8') as fh:
    json.dump(obj,fh,indent=2,sort_keys=True); fh.write('\n')
PY
chmod 600 "$INSTALL_DIR/install-manifest.json"

PATH_VALUE="$(dirname "$PYTHON_BIN"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
python3 - "$PLIST" "$LABEL" "$PYTHON_BIN" "$PY_ROOT" "$STATE_DIR" "$HOME" <<'PY'
import plistlib,sys
plist,label,python,pyroot,state,home=sys.argv[1:]
obj={
    'Label':label,
    'ProgramArguments':[python,'-m','llm_git_bridge.harness.cli','serve'],
    'EnvironmentVariables':{'PYTHONPATH':pyroot,'HOME':home,'PATH':'/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'},
    'RunAtLoad':True,
    'KeepAlive':True,
    'Umask':0o077,
    'StandardOutPath':state+'/stdout.log',
    'StandardErrorPath':state+'/stderr.log',
}
with open(plist,'wb') as fh:
    plistlib.dump(obj,fh,fmt=plistlib.FMT_XML,sort_keys=True)
PY
chmod 600 "$PLIST"
plutil -lint "$PLIST" >/dev/null

if [ "$ACTIVATE" -eq 0 ]; then
  echo "HARNESS_STAGED=1"
  echo "INSTALL_DIR=$INSTALL_DIR"
  echo "STATE_DIR=$STATE_DIR"
  echo "PLIST=$PLIST"
  echo "LABEL=$LABEL"
  echo "SAFARI_EXTENSION_DIR=$SAFARI_EXTENSION_DIR"
  exit 0
fi

command -v launchctl >/dev/null 2>&1 || { echo "ERROR: launchctl is required for --activate" >&2; exit 1; }
UID_NOW="$(id -u)"
# Deliberately operate on this label only. Never unload or restart the shell bridge.
launchctl bootout "gui/${UID_NOW}/${LABEL}" >/dev/null 2>&1 || true

# bootout may return before launchd has fully removed the service. Wait for the
# exact harness label to disappear before bootstrapping the replacement.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if ! launchctl print "gui/${UID_NOW}/${LABEL}" >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done
if launchctl print "gui/${UID_NOW}/${LABEL}" >/dev/null 2>&1; then
  echo "ERROR: harness LaunchAgent did not stop cleanly" >&2
  exit 1
fi

BOOTSTRAPPED=0
for _ in 1 2 3; do
  if launchctl bootstrap "gui/${UID_NOW}" "$PLIST" >/dev/null 2>&1; then
    BOOTSTRAPPED=1
    break
  fi
  # A bootstrap acknowledgement can fail after launchd has accepted the job.
  # Reconcile the exact label before deciding whether another attempt is needed.
  if launchctl print "gui/${UID_NOW}/${LABEL}" >/dev/null 2>&1; then
    BOOTSTRAPPED=1
    break
  fi
  sleep 0.3
done
if [ "$BOOTSTRAPPED" -ne 1 ]; then
  echo "ERROR: harness LaunchAgent bootstrap failed" >&2
  exit 1
fi
launchctl kickstart -k "gui/${UID_NOW}/${LABEL}"

for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if "$BIN_DIR/harness" call '{"protocol":1,"action":"ping","args":{}}' >/dev/null 2>&1 \n     && "$BIN_DIR/harness" browser-status >/dev/null 2>&1; then
    echo "HARNESS_ACTIVE=1"
    echo "SOCKET=$STATE_DIR/harness.sock"
    echo "SAFARI_EXTENSION_DIR=$SAFARI_EXTENSION_DIR"
    exit 0
  fi
  sleep 0.2
done

echo "ERROR: harness LaunchAgent started but acceptance checks did not succeed" >&2
exit 1
