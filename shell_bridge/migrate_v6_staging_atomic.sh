#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SUFFIX="${1:-candidate-$(git -C "$ROOT" rev-parse --short=12 HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
case "$SUFFIX" in *[!A-Za-z0-9._-]*|'') echo "invalid suffix" >&2; exit 2;; esac
HOME_ROOT="${MIGRATOR_HOME:-$HOME}"
UID_NOW="${MIGRATOR_UID:-$(id -u)}"
LAUNCHCTL="${MIGRATOR_LAUNCHCTL:-launchctl}"
RCLONE="${MIGRATOR_RCLONE:-rclone}"
OLD_LABEL="${MIGRATOR_OLD_LABEL:-net.laurenzo.local-executor-bridge-v6-staging}"
NEW_LABEL="net.laurenzo.local-executor-bridge-v6-$SUFFIX"
OLD_PLIST="$HOME_ROOT/Library/LaunchAgents/$OLD_LABEL.plist"
NEW_PLIST="$HOME_ROOT/Library/LaunchAgents/$NEW_LABEL.plist"
NEW_CONFIG="$HOME_ROOT/.config/local-executor-bridge-v6-$SUFFIX/config.json"
NEW_INSTALL="$HOME_ROOT/.local/share/local-executor-bridge-v6-$SUFFIX"
LOG_DIR="$HOME_ROOT/Library/Logs"
LOG_FILE="$LOG_DIR/LocalExecutorBridgeV6Migrator.log"
mkdir -p "$LOG_DIR"
chmod 700 "$LOG_DIR" 2>/dev/null || true
exec >>"$LOG_FILE" 2>&1
printf 'BEGIN %s suffix=%s\n' "$(date -u +%FT%TZ)" "$SUFFIX"
fail(){ echo "ERROR: $*" >&2; exit 1; }
need(){ command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"; }
need python3; need "$LAUNCHCTL"
PREVIOUS_LABEL=""
PREVIOUS_PLIST=""
NEW_STARTED=0
PREVIOUS_STOPPED=0
COMMITTED=0
HOLD_FILE="$LOG_DIR/LocalExecutorBridgeV6Migrator.hold"
[ ! -e "$HOLD_FILE" ] && [ ! -L "$HOLD_FILE" ] ||
  fail "previous staging migration left a reconciliation hold; do not retry or start a new owner"
consider_previous(){
  label="$1"; plist="$2"
  [ "$label" != "$NEW_LABEL" ] && [ "$label" != "$OLD_LABEL" ] || return 0
  if "$LAUNCHCTL" print "gui/$UID_NOW/$label" >/dev/null 2>&1; then
    [ -z "$PREVIOUS_LABEL" ] || fail "multiple active v6 staging services: $PREVIOUS_LABEL and $label"
    PREVIOUS_LABEL="$label"; PREVIOUS_PLIST="$plist"
  fi
}
rollback(){
  rc=$?
  if [ "$rc" -ne 0 ] && [ "$COMMITTED" -eq 0 ]; then
    echo "ROLLBACK rc=$rc"
    if [ "$NEW_STARTED" -eq 1 ]; then
      # Bootstrap can start even when launchctl returns nonzero, and a worker
      # may already have executed a side effect. Do not restart the old owner.
      "$LAUNCHCTL" bootout "gui/$UID_NOW/$NEW_LABEL" >/dev/null 2>&1 || true
      printf 'HOLD: v6 candidate may have admitted work. Do not restore previous staging owner without mailbox and journal reconciliation.\n' > "$HOLD_FILE"
      chmod 600 "$HOLD_FILE"
    elif [ "$PREVIOUS_STOPPED" -eq 1 ]; then
      # No new candidate was bootstrapped. Only restore an old owner whose
      # loaded launchd state can be independently inspected.
      if [ -n "$PREVIOUS_PLIST" ] && [ -f "$PREVIOUS_PLIST" ]; then
        if ! "$LAUNCHCTL" print "gui/$UID_NOW/$PREVIOUS_LABEL" >/dev/null 2>&1; then
          "$LAUNCHCTL" bootstrap "gui/$UID_NOW" "$PREVIOUS_PLIST" >/dev/null 2>&1 || true
        fi
        if ! "$LAUNCHCTL" print "gui/$UID_NOW/$PREVIOUS_LABEL" >/dev/null 2>&1; then
          printf 'HOLD: previous staging owner could not be restored. Inspect launchd before retry.\n' > "$HOLD_FILE"
          chmod 600 "$HOLD_FILE"
        fi
      else
        printf 'HOLD: previous staging owner has no verified rollback plist.\n' > "$HOLD_FILE"
        chmod 600 "$HOLD_FILE"
      fi
    fi
  fi
  exit "$rc"
}
trap rollback EXIT
[ -f "$OLD_PLIST" ] || fail "old v6 staging plist missing: $OLD_PLIST"
if "$LAUNCHCTL" print "gui/$UID_NOW/$OLD_LABEL" >/dev/null 2>&1; then
  PREVIOUS_LABEL="$OLD_LABEL"; PREVIOUS_PLIST="$OLD_PLIST"
fi
# Include manual-bootstrap, staging, candidate and every dynamic v6 label.
# The former candidate-only glob missed the actual duplicate staging daemon.
for plist in "$HOME_ROOT"/Library/LaunchAgents/net.laurenzo.local-executor-bridge-v6-*.plist; do
  [ -e "$plist" ] || continue
  label="$(basename "$plist" .plist)"
  consider_previous "$label" "$plist"
done
# Never boot out an already loaded candidate under the replacement label.
if "$LAUNCHCTL" print "gui/$UID_NOW/$NEW_LABEL" >/dev/null 2>&1; then
  fail "replacement v6 service is already loaded; reconcile before migration"
fi
if [ "${MIGRATOR_SKIP_STAGE:-0}" != 1 ]; then HOME="$HOME_ROOT" "$ROOT/shell_bridge/bootstrap_v6_noauth_stage.sh" "$SUFFIX"; fi
[ -f "$NEW_PLIST" ] || fail "candidate plist missing: $NEW_PLIST"
[ -f "$NEW_CONFIG" ] || fail "candidate config missing: $NEW_CONFIG"
[ -f "$NEW_INSTALL/bridge.py" ] || fail "candidate bridge missing"
[ -f "$NEW_INSTALL/approval_helper.py" ] || fail "candidate approval helper missing"
[ -f "$NEW_INSTALL/trusted_operations.py" ] || fail "candidate trusted operations module missing"
[ -f "$NEW_INSTALL/bridge_mailbox_inspector.py" ] || fail "candidate mailbox inspector missing"
[ -f "$NEW_INSTALL/ssh_pinned_helper.py" ] || fail "candidate pinned SSH helper missing"
CFG_LINE="$(python3 - "$NEW_CONFIG" <<'PYCFG'
import json,sys
c=json.load(open(sys.argv[1],encoding='utf-8'))
vals=[c.get('bridge_instance_id',''),c.get('remote',''),c.get('drive_root_folder_id','')]
if not all(isinstance(x,str) and x for x in vals): raise SystemExit(2)
print('\t'.join(vals))
PYCFG
)"
IFS=$'\t' read -r INSTANCE_ID REMOTE ROOT_ID <<< "$CFG_LINE"
[ -n "$INSTANCE_ID" ] && [ -n "$REMOTE" ] && [ -n "$ROOT_ID" ] || fail "staged v6 config is incomplete"
python3 - "$NEW_INSTALL/approval_helper.py" <<'PYAUTH'
import sys
s=open(sys.argv[1],encoding='utf-8').read()
for bad in ('LocalAuthentication','LAContext','evaluatePolicy','deviceOwnerAuthentication','userPresence'):
    if bad in s: raise SystemExit('forbidden auth primitive: '+bad)
if 'review(firstNew)' in s or 'function review(req)' in s: raise SystemExit('focus-stealing auto review remains')
if 'Allow once' not in s or 'Reject' not in s: raise SystemExit('menu decisions missing')
PYAUTH
if [ "${MIGRATOR_DRY_RUN:-0}" = 1 ]; then
  printf 'PLAN previous=%s new=%s config=%s install=%s\n' "${PREVIOUS_LABEL:-none}" "$NEW_LABEL" "$NEW_CONFIG" "$NEW_INSTALL"
  COMMITTED=1; trap - EXIT; exit 0
fi
if [ -n "$PREVIOUS_LABEL" ]; then
  PREVIOUS_STOPPED=1
  "$LAUNCHCTL" bootout "gui/$UID_NOW/$PREVIOUS_LABEL"
  if "$LAUNCHCTL" print "gui/$UID_NOW/$PREVIOUS_LABEL" >/dev/null 2>&1; then
    fail "old staging service remains loaded after bootout"
  fi
fi
# Mark possible candidate admission BEFORE the non-atomic bootstrap call.
NEW_STARTED=1
"$LAUNCHCTL" bootstrap "gui/$UID_NOW" "$NEW_PLIST"
"$LAUNCHCTL" kickstart -k "gui/$UID_NOW/$NEW_LABEL"
"$LAUNCHCTL" print "gui/$UID_NOW/$NEW_LABEL" >/dev/null
python3 "$NEW_INSTALL/bridge.py" doctor --config "$NEW_CONFIG" >/dev/null
if [ "${MIGRATOR_FAIL_AFTER_START:-0}" = 1 ]; then fail "injected post-start failure"; fi
if [ "${MIGRATOR_SKIP_HEALTH:-0}" != 1 ]; then
  need "$RCLONE"
  [ -f "$NEW_INSTALL/install-manifest.json" ] ||
    fail "staged v6 install manifest unavailable"
  SOURCE_COMMIT="$(python3 - "$NEW_INSTALL/install-manifest.json" <<'PYCOMMIT'
import json,re,sys
m=json.load(open(sys.argv[1],encoding='utf-8'))
v=m.get('source_commit')
if not isinstance(v,str) or not re.fullmatch(r'[a-f0-9]{40}',v): raise SystemExit(2)
print(v)
PYCOMMIT
)" || fail "staged v6 source commit is not pinned"
  HEALTH_VERIFIER="$ROOT/shell_bridge/verify_v6_candidate_health.py"
  [ -f "$HEALTH_VERIFIER" ] && [ ! -L "$HEALTH_VERIFIER" ] ||
    fail "exact-build v6 health verifier unavailable"
  tmp="$(mktemp)"; found=0
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if "$RCLONE" --drive-root-folder-id "$ROOT_ID" copyto "${REMOTE}health.json" "$tmp" >/dev/null 2>&1 &&
       PYTHONDONTWRITEBYTECODE=1 python3 "$HEALTH_VERIFIER" "$NEW_CONFIG" "$tmp" "$SOURCE_COMMIT" "$NEW_LABEL" >/dev/null 2>&1; then
      found=1; break
    fi
    sleep 1
  done
  rm -f "$tmp"
  [ "$found" -eq 1 ] ||
    fail "candidate exact build, service PID or fresh v6 health was not verified"
fi
COMMITTED=1
trap - EXIT
printf 'MIGRATED previous=%s new=%s instance=%s\n' "${PREVIOUS_LABEL:-none}" "$NEW_LABEL" "$INSTANCE_ID"
