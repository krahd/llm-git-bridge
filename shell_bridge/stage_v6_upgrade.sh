#!/bin/bash
# Prepare an immutable v6 replacement without starting a LaunchAgent or
# mutating the live production journal / Drive mailbox.
set -euo pipefail
fail(){ printf 'UPGRADE_STAGE_HOLD: %s\n' "$*" >&2; exit 1; }

[ "$#" -eq 0 ] || fail "no positional arguments accepted"
[ "$(uname -s)" = Darwin ] || fail "macOS required"
[ -z "${CHATGPT_SHELL_BRIDGE_REQUEST_ID:-}" ] ||
  fail "a bridge request cannot stage a replacement of its own production service"
[ -n "${SOURCE_CONFIG_FILE:-}" ] && [ -n "${EXPECTED_SOURCE_COMMIT:-}" ] ||
  fail "SOURCE_CONFIG_FILE and EXPECTED_SOURCE_COMMIT are required"
[ -n "${UPGRADE_SUFFIX:-}" ] || fail "an explicit fresh UPGRADE_SUFFIX is required"
case "$UPGRADE_SUFFIX" in *[!a-zA-Z0-9._-]*|'') fail "invalid suffix" ;; esac
case "$EXPECTED_SOURCE_COMMIT" in *[!a-f0-9]*|'') fail "invalid pinned revision" ;; esac
[ "${#EXPECTED_SOURCE_COMMIT}" -eq 40 ] || fail "pinned revision must be full 40-hex SHA"

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
[ "$(git -C "$ROOT" rev-parse HEAD)" = "$EXPECTED_SOURCE_COMMIT" ] ||
  fail "staging checkout is not the pinned source revision"
for path in shell_bridge/install.sh shell_bridge/stage_v6_upgrade.sh \
            shell_bridge/bridge.py shell_bridge/workspace.py \
            shell_bridge/approval_helper.py shell_bridge/trusted_operations.py \
            shell_bridge/bridge_mailbox_inspector.py \
            shell_bridge/ssh_pinned_helper.py shell_bridge/approval_gui.swift \
            shell_bridge/assets/bridge-menubar.png \
            shell_bridge/verify_upgrade_stage_source.py; do
  git -C "$ROOT" ls-files --error-unmatch -- "$path" >/dev/null ||
    fail "untracked installation input is not covered by the pinned revision"
  git -C "$ROOT" diff --quiet HEAD -- "$path" ||
    fail "source checkout has modified installation inputs"
done

# The config values are printed *only* through a strict, bounded envelope
# before being interpreted as environment variable values. Never emit OAuth
# fields or other private configuration contents.
line="$(python3 - "$SOURCE_CONFIG_FILE" <<'PY'
import json,re,sys
from pathlib import Path
p=Path(sys.argv[1])
if not p.is_absolute() or p.is_symlink() or not p.is_file():
    raise SystemExit("source config unavailable")
c=json.loads(p.read_text(encoding='utf-8'))
keys=('remote','base_path','allowed_root','state_dir','shell')
values=[c.get(k) for k in keys]
if any(not isinstance(v,str) or not v or len(v)>2048 or
       re.search(r'[\x00-\x1f\x7f]',v) for v in values):
    raise SystemExit("source identity incomplete or unsafe")
print('\t'.join(values))
PY
)" || fail "could not validate source identity fields"
IFS=$'\t' read -r RCLONE_REMOTE BASE_PATH ALLOWED_ROOT STATE_DIR SHELL_BIN <<<"$line"
export SOURCE_CONFIG_FILE RCLONE_REMOTE BASE_PATH ALLOWED_ROOT STATE_DIR SHELL_BIN

LABEL="net.laurenzo.local-executor-bridge-v6-$UPGRADE_SUFFIX"
LOCAL_EXECUTOR_LABEL="$LABEL"
INSTALL_DIR="$HOME/.local/share/local-executor-bridge-v6-$UPGRADE_SUFFIX"
CONFIG_DIR="$HOME/.config/local-executor-bridge-v6-$UPGRADE_SUFFIX"
# Park the prepared plist outside ~/Library/LaunchAgents. A LaunchAgent
# sitting there may be automatically loaded on a later login even when
# install.sh did not explicitly bootstrap it.
PLIST="$INSTALL_DIR/launchagent.plist"
export LOCAL_EXECUTOR_LABEL INSTALL_DIR CONFIG_DIR PLIST
[ ! -e "$INSTALL_DIR" ] && [ ! -e "$CONFIG_DIR" ] && [ ! -e "$PLIST" ] ||
  fail "fresh candidate path already exists; do not overwrite it"

# install.sh --upgrade-stage-only is restricted to local fresh artefacts.
# It proves every mailbox and durable-state identifier against the
# read-only source before creating the candidate. It never touches
# production state files or writes to the production Drive mailbox.
"$ROOT/shell_bridge/install.sh" --upgrade-stage-only
printf 'V6_PRODUCTION_CANDIDATE_STAGED_ONLY=1\n'
printf 'CANDIDATE_LABEL=%s\n' "$LABEL"
