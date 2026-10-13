#!/bin/bash
# Explicit v6-to-v6 entrypoint. Fail closed; never select the v5-first mode.
# This is not a remediation utility for duplicate owners or indeterminate work.
set -euo pipefail

fail(){ printf 'V6_UPGRADE_HOLD: %s\n' "$*" >&2; exit 1; }

[ "$#" -eq 0 ] || fail "no arguments accepted; stage a verified candidate separately"
[ "$(uname -s)" = Darwin ] || fail "macOS required"
[ -t 0 ] || fail "out-of-band interactive local Terminal required"
[ -z "${CHATGPT_SHELL_BRIDGE_REQUEST_ID:-}" ] ||
  fail "self-hosted mailbox execution is not a permitted cutover controller"
[ -n "${LOCAL_EXECUTOR_LABEL:-}" ] ||
  fail "explicit fresh candidate LOCAL_EXECUTOR_LABEL required"
[ -n "${EXPECTED_SOURCE_COMMIT:-}" ] ||
  fail "pin expected candidate build commit"
[ -n "${NEW_PLIST:-}" ] && [ -n "${CONFIG_FILE:-}" ] &&
[ -n "${INSTALL_DIR:-}" ] && [ -n "${LEGACY_STATE_DIR:-}" ] ||
  fail "fresh candidate plist, config, installation and old journal paths must be explicit"

case "$LOCAL_EXECUTOR_LABEL" in
  net.laurenzo.local-executor-bridge-v6-*) ;;
  *) fail "replacement must use a fresh v6-specific LaunchAgent label" ;;
esac
case "$EXPECTED_SOURCE_COMMIT" in
  *[!a-f0-9]*|'') fail "expected commit must be lower-case hexadecimal" ;;
esac
[ "${#EXPECTED_SOURCE_COMMIT}" -eq 40 ] ||
  fail "expected commit must be an exact 40-hex revision"

# The controller performs one pre-stop snapshot and, after stopping the
# source owner, repeats mailbox + journal drains. If the new bridge might
# have executed anything, rollback holds rather than replaying old work.
export CUTOVER_SOURCE_VERSION=6
exec /bin/bash "$(cd "$(dirname "$0")" && pwd -P)/cutover.sh"
