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
# Atomic per-build lock prevents two invocations from racing to create the
# same remote mailbox. A stale lock is not automatically stolen.
lock_parent="$HOME/.local/state"
[ -d "$lock_parent" ] || { echo "HOLD: bridge state root missing" >&2; exit 6; }
lockdir="$lock_parent/.v6-preflight-${source_commit:0:12}.lock"
if ! mkdir "$lockdir" 2>/dev/null; then
  echo "HOLD: same-build staging already running or stale lock: $lockdir" >&2
  exit 6
fi
trap 'rm -f "${report:-}" "${health_json:-}" "${smoke_request:-}" "${smoke_result:-}"; rmdir "$lockdir" >/dev/null 2>&1 || true' EXIT
suffix="candidate-${source_commit:0:12}"
label="net.laurenzo.local-executor-bridge-v6-${suffix}"
config="$HOME/.config/local-executor-bridge-v6-${suffix}/config.json"
plist="$HOME/Library/LaunchAgents/${label}.plist"
install="$HOME/.local/share/local-executor-bridge-v6-${suffix}"
state="$HOME/.local/state/local-executor-bridge-v6-${suffix}"
if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
  echo "HOLD: candidate label already loaded; will not overwrite running service" >&2; exit 6
fi
if [ -e "$config" ] || [ -L "$config" ] || [ -e "$plist" ] || [ -L "$plist" ] || \
  [ -e "$install" ] || [ -L "$install" ] || [ -e "$state" ] || [ -L "$state" ]; then
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
candidate_base="Local Executor Bridge v6 Candidate ${source_commit:0:12}"
remote_dirs="$(rclone lsjson "$RCLONE_REMOTE" --dirs-only --max-depth 1)" || {
  echo "HOLD: Drive mailbox inventory failed; no changes made" >&2
  exit 7
}
python3 - "$remote_dirs" "$candidate_base" <<'PYREMOTEID'
import json,sys
try:
    records=json.loads(sys.argv[1])
    matches=[x for x in records if x.get('IsDir') and x.get('Name')==sys.argv[2]]
except (ValueError,TypeError,AttributeError):
    raise SystemExit('HOLD: invalid remote directory inventory')
if matches:
    raise SystemExit('HOLD: candidate mailbox already exists remotely; never adopt an unknown consumer mailbox')
PYREMOTEID
allowed_root="${ALLOWED_ROOT:-$HOME/tom-repos}"
python3 - "$ROOT" "$allowed_root" <<'PYALLOWED'
import pathlib,sys
repo=pathlib.Path(sys.argv[1]).resolve()
allowed=pathlib.Path(sys.argv[2]).expanduser().resolve()
try:
    repo.relative_to(allowed)
except ValueError:
    raise SystemExit('HOLD: source checkout is outside the selected allowed root')
if not allowed.is_dir():
    raise SystemExit('HOLD: allowed root is not a directory')
PYALLOWED
ALLOWED_ROOT="$allowed_root" \
BASE_PATH="$candidate_base" \
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
printf '\n=== Read-only end-to-end staging smoke ===\n'
smoke_id="v6-smoke-$(date -u +%Y%m%d%H%M%S)-$$"
smoke_request="$(mktemp)"
smoke_result="$(mktemp)"
python3 - "$smoke_request" "$smoke_id" "$ROOT" <<'PYREQUEST'
import json,sys
path,rid,cwd=sys.argv[1:]
request={
    "protocol":1,
    "id":rid,
    "cwd":cwd,
    "command":"printf 'ISOLATED_V6_SMOKE_OK\\n'",
    "explanation":"Non-mutating local v6 staging acceptance canary",
    "timeout_seconds":20,
    "write_scope":"read_only",
}
with open(path,"w",encoding="utf-8") as f:
    json.dump(request,f,separators=(",",":"),sort_keys=True)
PYREQUEST
# Submit once. A failed acknowledgement is ambiguous, so never resubmit
# this request ID; preserve its identifier in the failure message.
if ! rclone --drive-root-folder-id "$root_id" --timeout 20s copyto \
  "$smoke_request" "${RCLONE_REMOTE}requests/${smoke_id}.json"; then
  echo "HOLD: smoke submission ambiguous, id=$smoke_id; do not replay it" >&2
  exit 10
fi
smoke_ok=0
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  if rclone --drive-root-folder-id "$root_id" --timeout 20s copyto \
    "${RCLONE_REMOTE}results/${smoke_id}.json" "$smoke_result" >/dev/null 2>&1 &&
    PYTHONDONTWRITEBYTECODE=1 python3 shell_bridge/verify_v6_candidate_smoke.py \
      "$smoke_request" "$smoke_result" "$smoke_id" >/dev/null 2>&1; then
    smoke_ok=1
    break
  fi
  sleep 2
done
if [ "$smoke_ok" -ne 1 ]; then
  echo "HOLD: isolated read-only request not verified, id=$smoke_id; preserve journal/result" >&2
  exit 11
fi
PYTHONDONTWRITEBYTECODE=1 python3 shell_bridge/verify_v6_candidate_smoke.py \
  "$smoke_request" "$smoke_result" "$smoke_id"
printf '\nISOLATED_V6_RUNNING=1\nLABEL=%s\nCONFIG=%s\nSOURCE=%s\n' "$label" "$config" "$source_commit"
printf 'Production v5 unchanged. Cutover still requires live approval, replay and recovery acceptance.\n'
