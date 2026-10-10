#!/bin/bash
# Single invocation: qualify isolated candidate, exercise native approval,
# stage exact-main production, perform guarded provisional cutover, verify
# production approval, preserve rollback. Never silently erase old state.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$ROOT"
fail(){ printf 'HOLD: %s\n' "$*" >&2; exit 1; }
[ "$(uname -s)" = Darwin ] || fail "requires macOS"
[ "$#" -eq 0 ] || fail "usage: bash shell_bridge/v6_release_once.sh"
[ -z "${CHATGPT_SHELL_BRIDGE_REQUEST_ID:-}" ] || fail "run from a local Terminal, never the bridge being replaced"
for tool in git python3 rclone launchctl; do
  command -v "$tool" >/dev/null 2>&1 || fail "missing $tool"
done
[ -z "$(git status --porcelain)" ] || fail "release worktree is dirty"
SOURCE="$(git rev-parse HEAD)"
[ "${#SOURCE}" -eq 40 ] || fail "invalid source identity"
echo "RELEASE_SOURCE=$SOURCE"
echo "Phase 1: macOS regressions, live owner inventory, isolated v6 startup and smoke"
REGISTER_MAILBOX_INSPECTOR=1 bash shell_bridge/prepare_v6_on_mac.sh
# The preparation script created exactly this isolated and uniquely named build.
SUFFIX="candidate-${SOURCE:0:12}"
CANDIDATE_CONFIG="$HOME/.config/local-executor-bridge-v6-$SUFFIX/config.json"
CANDIDATE_LABEL="net.laurenzo.local-executor-bridge-v6-$SUFFIX"

config_value(){
  python3 - "$1" "$2" <<'PYVAL'
import json,sys
value=json.load(open(sys.argv[1],encoding="utf-8")).get(sys.argv[2])
if not isinstance(value,str) or not value or not value.isprintable():
    raise SystemExit("missing bridge identity field")
print(value)
PYVAL
}
REMOTE="$(config_value "$CANDIDATE_CONFIG" remote)"
CANDIDATE_ROOT="$(config_value "$CANDIDATE_CONFIG" drive_root_folder_id)"
CANDIDATE_ALLOWED="$(config_value "$CANDIDATE_CONFIG" allowed_root)"
[ "$CANDIDATE_ROOT" != "1PRsQHgVsgXhIYT_8akGFRGdhdlw9Lmk6" ] || fail "candidate is bound to the production mailbox"
python3 - "$CANDIDATE_CONFIG" "$CANDIDATE_ALLOWED" <<'PYREG'
import json,sys
from pathlib import Path
sys.path.insert(0,"shell_bridge")
from trusted_operations import registered_operation_from_config
c=json.load(open(sys.argv[1],encoding="utf-8"))
registered_operation_from_config(c,"bridge-mailbox-inspect","inspect",Path(sys.argv[2]))
print("PINNED_READ_ONLY_APPROVAL_HELPER=1")
PYREG

approval_canary(){
  local root="$1" allowed="$2" decision="$3" prefix="$4"
  local rid request_file result_file attempts found=0
  rid="v6-approval-$prefix-$(date -u +%Y%m%d%H%M%S)-$$-$decision"
  request_file="$(mktemp)"; result_file="$(mktemp)"
  # One request, one identity. Never resubmit following transport ambiguity.
  python3 shell_bridge/verify_v6_approval.py create "$request_file" "$rid" "$allowed" "$decision" || {
    rm -f "$request_file" "$result_file"; return 1
  }
  if ! rclone --drive-root-folder-id "$root" --timeout 20s copyto "$request_file" "${REMOTE}requests/$rid.json"; then
    echo "HOLD: ambiguous approval request upload $rid; reconcile before retrying" >&2
    rm -f "$request_file" "$result_file"; return 1
  fi
  echo "NATIVE_APPROVAL_ACTION_REQUIRED=$decision (request $rid)"
  if [ "$decision" = deny ]; then
    echo "Choose DENY in the native popup. This must prevent execution."
  else
    echo "Choose APPROVE in the native popup for the SHA-pinned read-only inventory."
  fi
  for attempts in {1..75}; do
    if rclone --drive-root-folder-id "$root" --timeout 20s copyto "${REMOTE}results/$rid.json" "$result_file" >/dev/null 2>&1; then
      found=1; break
    fi
    sleep 4
  done
  if [ "$found" -ne 1 ]; then
    echo "HOLD: approval response missing/ambiguous, request=$rid" >&2
    rm -f "$request_file" "$result_file"; return 1
  fi
  if ! PYTHONDONTWRITEBYTECODE=1 python3 shell_bridge/verify_v6_approval.py verify "$request_file" "$rid" "$result_file" "$decision"; then
    echo "HOLD: approval result failed identity/decision verification for $rid" >&2
    rm -f "$request_file" "$result_file"; return 1
  fi
  rm -f "$request_file" "$result_file"
}
echo "Phase 2: two critical one-time native UI acceptance decisions"
approval_canary "$CANDIDATE_ROOT" "$CANDIDATE_ALLOWED" deny staging
approval_canary "$CANDIDATE_ROOT" "$CANDIDATE_ALLOWED" allow staging

echo "Phase 3: stage immutable production v6 without starting it"
LEGACY_CONFIG="$HOME/.config/chatgpt-shell-bridge/config.json"
[ -f "$LEGACY_CONFIG" ] && [ ! -L "$LEGACY_CONFIG" ] || fail "canonical v5 config missing or symlinked"
LEGACY_METADATA="$(python3 - "$LEGACY_CONFIG" <<'PYV5'
import json,sys
c=json.load(open(sys.argv[1],encoding="utf-8"))
fields=("remote","base_path","drive_root_folder_id","requests_folder_id",
        "results_folder_id","allowed_root","state_dir","bridge_instance_id")
values=[c.get(x) for x in fields]
if any(not isinstance(v,str) or not v or '\t' in v or '\n' in v for v in values):
    raise SystemExit("production v5 configuration incomplete")
if c["drive_root_folder_id"]!="1PRsQHgVsgXhIYT_8akGFRGdhdlw9Lmk6":
    raise SystemExit("production root differs from pinned v5 identity")
print("\t".join(values))
PYV5
)" || fail "could not verify actual v5 identity"
IFS=$'\t' read -r PROD_REMOTE PROD_BASE PROD_ROOT PROD_REQ PROD_RES PROD_ALLOWED PROD_STATE PROD_INSTANCE <<< "$LEGACY_METADATA"
[ "$REMOTE" = "$PROD_REMOTE" ] || fail "staged candidate uses a different rclone remote"
[ "$PROD_STATE" = "$HOME/.local/state/chatgpt-shell-bridge" ] || fail "legacy durable state path changed; reconcile manually"
[ -d "$PROD_ALLOWED" ] && [ -d "$PROD_STATE" ] || fail "v5 root/state not accessible"
PROD_LABEL="net.laurenzo.local-executor-bridge-v6-production-${SOURCE:0:12}"
PROD_INSTALL="$HOME/.local/share/local-executor-bridge-v6-production-${SOURCE:0:12}"
PROD_CONFIG_DIR="$HOME/.config/local-executor-bridge-v6-production-${SOURCE:0:12}"
PROD_CONFIG="$PROD_CONFIG_DIR/config.json"
PROD_PLIST="$HOME/Library/LaunchAgents/$PROD_LABEL.plist"
if launchctl print "gui/$(id -u)/$PROD_LABEL" >/dev/null 2>&1 ||
   [ -e "$PROD_INSTALL" ] || [ -L "$PROD_INSTALL" ] ||
   [ -e "$PROD_CONFIG_DIR" ] || [ -L "$PROD_CONFIG_DIR" ] ||
   [ -e "$PROD_PLIST" ] || [ -L "$PROD_PLIST" ]; then
  fail "production v6 installation is already present; never overwrite/replay"
fi
REGISTER_MAILBOX_INSPECTOR=1 \
RCLONE_REMOTE="$PROD_REMOTE" BASE_PATH="$PROD_BASE" \
ALLOWED_ROOT="$PROD_ALLOWED" STATE_DIR="$PROD_STATE" \
LOCAL_EXECUTOR_LABEL="$PROD_LABEL" INSTALL_DIR="$PROD_INSTALL" \
CONFIG_DIR="$PROD_CONFIG_DIR" PLIST="$PROD_PLIST" \
bash shell_bridge/install.sh --stage-only
python3 - "$PROD_CONFIG" "$PROD_ROOT" "$PROD_REQ" "$PROD_RES" "$PROD_STATE" <<'PYMATCH'
import json,sys
c=json.load(open(sys.argv[1],encoding="utf-8"))
for key,value in zip(("drive_root_folder_id","requests_folder_id",
                      "results_folder_id","state_dir"),sys.argv[2:]):
    if c.get(key)!=value:
        raise SystemExit("HOLD: production stage differs from v5 "+key)
PYMATCH
python3 "$PROD_INSTALL/bridge.py" doctor --config "$PROD_CONFIG" >/dev/null
# Only a local operator may authorize transferring the production mailbox.
echo "Phase 4: consequential cutover authorization"
printf 'Ready to stop v5 and provisionally activate v6. Type MIGRATE to proceed: '
IFS= read -r authorization
[ "$authorization" = MIGRATE ] || fail "operator did not authorize production handover"
echo "Phase 5: guarded production cutover with no automatic legacy retirement"
EXPECTED_SOURCE_COMMIT="$SOURCE" \
LOCAL_EXECUTOR_LABEL="$PROD_LABEL" NEW_PLIST="$PROD_PLIST" \
INSTALL_DIR="$PROD_INSTALL" CONFIG_FILE="$PROD_CONFIG" \
LEGACY_STATE_DIR="$PROD_STATE" RETIRE_OLD_AFTER_SMOKE=0 \
bash shell_bridge/cutover.sh || fail "production cutover did not complete; inspect fail-closed rollback evidence"

echo "Phase 6: production native approval acceptance"
approval_canary "$PROD_ROOT" "$PROD_ALLOWED" allow production || {
  echo "HOLD: production is provisional. Preserve rollback records and inspect approval failure." >&2
  exit 14
}
python3 - "$PROD_STATE/cutover-provisional.txt" "$SOURCE" <<'PYFINAL'
from pathlib import Path
import sys
p=Path(sys.argv[1])
text=p.read_text(encoding="utf-8")
if "state=provisional" not in text or "source="+sys.argv[2] not in text:
    raise SystemExit("production provisional receipt is missing or from wrong build")
print("V6_PRODUCTION_PROVISIONAL_AND_APPROVAL_VERIFIED=1")
PYFINAL
echo "Production v6 is provisionally active and passed native approval acceptance."
echo "Legacy plists and journals are preserved for rollback; retiring old staging services requires an independently verified idle-state audit."
