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
[ "$(git rev-parse refs/remotes/origin/main 2>/dev/null)" = "$SOURCE" ] || fail "this worktree is not the freshly fetched canonical origin/main release"
echo "RELEASE_SOURCE=$SOURCE"
if [ -z "${V6_SSH_POLICY_DIR:-}" ]; then
  echo "One-time operator enrollment: select an existing verified SSH host."
  V6_SSH_POLICY_DIR="$(PYTHONDONTWRITEBYTECODE=1 python3 shell_bridge/setup_v6_pinned_ssh.py "$SOURCE" "${ALLOWED_ROOT:-$HOME/tom-repos}")" ||
    fail "SSH enrollment incomplete; production v5 has not been modified"
fi
REGISTER_PINNED_SSH_POLICY_DIR="$V6_SSH_POLICY_DIR"
export REGISTER_PINNED_SSH_POLICY_DIR
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
ssh_status_canary(){
  local root="$1" allowed="$2" prefix="$3"
  local rid request_file result_file found=0 attempt
  rid="v6-ssh-$prefix-$(date -u +%Y%m%d%H%M%S)-$"
  request_file="$(mktemp)"; result_file="$(mktemp)"
  if ! python3 shell_bridge/verify_v6_pinned_ssh.py create "$request_file" "$rid" "$allowed"; then
    rm -f "$request_file" "$result_file"; return 1
  fi
  if ! rclone --drive-root-folder-id "$root" --timeout 20s copyto "$request_file" "${REMOTE}requests/$rid.json"; then
    echo "HOLD: ambiguous SSH smoke submission ID $rid; never replay" >&2
    rm -f "$request_file" "$result_file"; return 1
  fi
  for attempt in {1..30}; do
    if rclone --drive-root-folder-id "$root" --timeout 20s copyto "${REMOTE}results/$rid.json" "$result_file" >/dev/null 2>&1; then
      found=1; break
    fi
    sleep 3
  done
  if [ "$found" -ne 1 ]; then
    echo "HOLD: pinned SSH smoke result not received, ID $rid" >&2
    rm -f "$request_file" "$result_file"; return 1
  fi
  if ! python3 shell_bridge/verify_v6_pinned_ssh.py verify "$request_file" "$rid" "$result_file"; then
    echo "HOLD: pinned SSH smoke denied or unverified, ID $rid" >&2
    rm -f "$request_file" "$result_file"; return 1
  fi
  rm -f "$request_file" "$result_file"
}
echo "Phase 2: two critical one-time native UI acceptance decisions"
approval_canary "$CANDIDATE_ROOT" "$CANDIDATE_ALLOWED" deny staging
approval_canary "$CANDIDATE_ROOT" "$CANDIDATE_ALLOWED" allow staging
ssh_status_canary "$CANDIDATE_ROOT" "$CANDIDATE_ALLOWED" staging

echo "Phase 2b: verify macOS inbound Remote Login and a real separate-device SSH session"
PYTHONDONTWRITEBYTECODE=1 python3 shell_bridge/inbound_ssh_preflight.py ||
  fail "inbound Remote Login not safely configured; preserve v5 and configure access locally"
[ -d "$HOME/.local/state" ] || fail "SSH witness state directory is missing"
SSH_WITNESS_DIR="$(mktemp -d "$HOME/.local/state/v6-inbound-ssh.XXXXXXXX")" ||
  fail "cannot reserve a private SSH witness directory"
SSH_WITNESS_NONCE="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
SSH_WITNESS_FILE="$SSH_WITNESS_DIR/verified.json"
echo "From a DIFFERENT computer, establish an authenticated SSH login to this Mac."
echo "Inside that inbound SSH session, run this exact read-only witness command:"
printf 'python3 %q create %q %q\n' \
  "$ROOT/shell_bridge/inbound_ssh_witness.py" "$SSH_WITNESS_FILE" "$SSH_WITNESS_NONCE"
echo "The challenge expires after 3 minutes. No inbound SSH settings are changed."
inbound_confirmed=0
for attempt in {1..36}; do
  if PYTHONDONTWRITEBYTECODE=1 python3 shell_bridge/inbound_ssh_witness.py verify \
    "$SSH_WITNESS_FILE" "$SSH_WITNESS_NONCE" >/dev/null 2>&1; then
    inbound_confirmed=1
    break
  fi
  sleep 5
done
[ "$inbound_confirmed" = 1 ] ||
  fail "external-device SSH login witness missing or invalid; v5 has not been stopped"
echo "NONLOCAL_INBOUND_SSH_SESSION_WITNESS_VERIFIED=1"

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
# The stage-only installer may create a missing mailbox. Never let that
# happen to production: prove the legacy named folder resolves to the exact
# already-running pinned production folder ID first.
PROD_DIRECTORY_INVENTORY="$(rclone lsjson "$PROD_REMOTE" --dirs-only --max-depth 1)" ||
  fail "cannot verify legacy production mailbox path"
python3 - "$PROD_DIRECTORY_INVENTORY" "$PROD_BASE" "$PROD_ROOT" <<'PYV5PATH'
import json,sys
items=json.loads(sys.argv[1])
base,expected=sys.argv[2:]
matches=[x for x in items if isinstance(x,dict) and
         x.get("IsDir") and x.get("Name")==base]
if len(matches)!=1 or matches[0].get("ID")!=expected:
    raise SystemExit("HOLD: v5 mailbox path cannot be resolved to pinned production folder")
PYV5PATH
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
python3 - "$PROD_CONFIG" "$PROD_ROOT" "$PROD_REQ" "$PROD_RES" "$PROD_STATE" "$PROD_INSTANCE" <<'PYMATCH'
import json,sys
c=json.load(open(sys.argv[1],encoding="utf-8"))
for key,value in zip(("drive_root_folder_id","requests_folder_id",
                      "results_folder_id","state_dir","bridge_instance_id"),sys.argv[2:]):
    if c.get(key)!=value:
        raise SystemExit("HOLD: production stage differs from v5 "+key)
PYMATCH
python3 "$PROD_INSTALL/bridge.py" doctor --config "$PROD_CONFIG" >/dev/null
# Only a local operator may authorize transferring the production mailbox.
echo "Phase 4: consequential cutover authorization"
echo "IMPORTANT: v6 currently supports only pinned SSH status/identity actions."
echo "Arbitrary SSH, SCP, SFTP and RSYNC are NOT yet qualified as v5 replacement capabilities."
echo "If those workflows are required, STOP here and keep v5 as production."
printf 'To deliberately accept this limited SSH scope and switch production, type MIGRATE LIMITED SSH: '
IFS= read -r authorization
[ "$authorization" = "MIGRATE LIMITED SSH" ] ||
  fail "operator did not accept limited SSH capability and authorize production handover"
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
ssh_status_canary "$PROD_ROOT" "$PROD_ALLOWED" production || fail "production SSH read-only acceptance failed; hold v6 provisional and preserve rollback"
echo "Phase 7: verify exactly one loaded production mailbox owner"
LIVE_OWNERSHIP="$(python3 shell_bridge/bridge_service_ownership.py)" ||
  fail "post-cutover live bridge inventory is not verifiable"
python3 - "$LIVE_OWNERSHIP" "$PROD_ROOT" "$PROD_LABEL" <<'PYEXCLUSIVE'
import json,sys
report=json.loads(sys.argv[1])
root,label=sys.argv[2:]
if report.get("problems") or not report.get("safe_to_stage"):
    raise SystemExit("HOLD: post-cutover ownership is ambiguous")
owners=[x["label"] for x in report.get("loaded",[])
        if x.get("drive_root_folder_id")==root]
if owners!=[label]:
    raise SystemExit("HOLD: v6 does not have exclusive production mailbox ownership")
print("PRODUCTION_MAILBOX_EXCLUSIVE_OWNER_VERIFIED=1")
PYEXCLUSIVE
echo "Phase 8: archive stopped production v5 LaunchAgents to prevent restart at next login"
ROLLBACK_LIST="$PROD_STATE/cutover-rollback-services.tsv"
[ -s "$ROLLBACK_LIST" ] || fail "rollback inventory missing; do not retire or claim completion"
ARCHIVE_DIR="$HOME/Library/LaunchAgents/retired-v5-${SOURCE:0:12}"
[ ! -e "$ARCHIVE_DIR" ] && [ ! -L "$ARCHIVE_DIR" ] || fail "old LaunchAgent archive exists; reconcile before replay"
declare -a OLD_PLISTS=()
declare -a OLD_ARCHIVED=()
while IFS='|' read -r label plist; do
  [ -n "$label" ] || continue
  case "$label" in
    io.llm-git-bridge.daemon|com.tom.chatgpt-shell-bridge|net.laurenzo.mac-executor-bridge|net.laurenzo.local-executor-bridge) ;;
    *) fail "unexpected legacy label in rollback evidence: $label" ;;
  esac
  [ "$plist" = "$HOME/Library/LaunchAgents/$label.plist" ] || fail "unexpected rollback plist path"
  [ -f "$plist" ] && [ ! -L "$plist" ] || fail "legacy plist missing/symlinked: $label"
  if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
    fail "legacy production service is loaded again; refuse retirement"
  fi
  OLD_PLISTS+=("$plist")
  OLD_ARCHIVED+=("$ARCHIVE_DIR/$label.plist")
done < "$ROLLBACK_LIST"
# Historical non-loaded v5 alias can be configured to auto-load on a later
# login. Archive its top-level plist as well, but only when not currently loaded.
LEGACY_ALIAS="$HOME/Library/LaunchAgents/com.tom.chatgpt-shell-bridge.plist"
if [ -e "$LEGACY_ALIAS" ] || [ -L "$LEGACY_ALIAS" ]; then
  [ -f "$LEGACY_ALIAS" ] && [ ! -L "$LEGACY_ALIAS" ] ||
    fail "legacy alias plist is not a regular file"
  if launchctl print "gui/$(id -u)/com.tom.chatgpt-shell-bridge" >/dev/null 2>&1; then
    fail "legacy alias is unexpectedly loaded; cannot safely archive it"
  fi
  alias_found=0
  for existing in "${OLD_PLISTS[@]}"; do
    [ "$existing" = "$LEGACY_ALIAS" ] && alias_found=1
  done
  if [ "$alias_found" -eq 0 ]; then
    OLD_PLISTS+=("$LEGACY_ALIAS")
    OLD_ARCHIVED+=("$ARCHIVE_DIR/com.tom.chatgpt-shell-bridge.plist")
  fi
fi
[ "${#OLD_PLISTS[@]}" -gt 0 ] || fail "no stopped production services available for archiving"
launchctl print "gui/$(id -u)/$PROD_LABEL" >/dev/null 2>&1 || fail "production v6 is no longer loaded"
mkdir -m 700 "$ARCHIVE_DIR"
# Each rename is reversible. On partial failure preserve the archive and
# never restart v5 automatically while v6 may have executed effects.
for ((i=0;i<${#OLD_PLISTS[@]};i++)); do
  mv -n "${OLD_PLISTS[i]}" "${OLD_ARCHIVED[i]}" || fail "failed to archive legacy service, preserve partial evidence"
  [ -f "${OLD_ARCHIVED[i]}" ] && [ ! -e "${OLD_PLISTS[i]}" ] ||
    fail "legacy archive postcondition failed"
done
python3 - "$PROD_STATE/v6-release-acceptance.json" "$SOURCE" "$PROD_LABEL" "$ARCHIVE_DIR" <<'PYACCEPT'
import json,os,sys,tempfile,time
path,source,label,archive=sys.argv[1:]
receipt={
    "schema":1,"kind":"bridge_v6_production_acceptance",
    "source_commit":source, "production_label":label,
    "native_operator_approval_verified":True,
    "v5_launchagents_archived":archive,
    "legacy_state_preserved":True,
    "recovery_fault_injection_live_verified":False,
    "accepted_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
}
fd,tmp=tempfile.mkstemp(prefix=".v6-acceptance-",dir=os.path.dirname(path))
try:
    with os.fdopen(fd,"w",encoding="utf-8") as f:
        json.dump(receipt,f,sort_keys=True,indent=2)
        f.write(chr(10));f.flush();os.fsync(f.fileno())
    os.chmod(tmp,0o600)
    os.replace(tmp,path)
finally:
    if os.path.exists(tmp):os.unlink(tmp)
PYACCEPT
echo "V6_PRODUCTION_ACCEPTED_WITH_RECOVERY_EVIDENCE_PRESERVED=1"
echo "Historical v5 production launch agents archived; other staging instances are retained until separately reconciled."

