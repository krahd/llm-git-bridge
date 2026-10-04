#!/bin/zsh
set -eu
umask 077

mode="${1:-install}"
if [[ "$mode" != "install" && "$mode" != "--dry-run" ]]; then
  echo "usage: $0 [--dry-run]" >&2
  exit 64
fi

stage="initialization"
repo="/Users/tom/tom-repos/projects/llm-git-bridge/.bridge-worktrees/v5-popup-exact-20261004"
src="$repo/shell_bridge/bridge.py"
dst="$HOME/.local/share/chatgpt-shell-bridge/bridge.py"
dir="$HOME/.local/share/chatgpt-shell-bridge"
python="/opt/homebrew/bin/python3"
label="gui/$(id -u)/com.tom.chatgpt-shell-bridge"
expected_branch="maintenance/v5-popup-exact-20261004"
expected_bridge_blob="99e5c8f6075227a826c56bce4579920c74958357"
expected_src_sha="6774d91e916f838e9dab5a8a44498759fd4d740d64b2a8a0833fee4287f62766"
expected_old_sha="10e0654a230abd04f1a715342f144a62588373e4cd072f36336d728ad761ae8d"

tmp=""
restore_tmp=""
backup=""
installed=0

sha256_file() {
  local line
  line="$(shasum -a 256 -- "$1")" || return 1
  REPLY="${line%% *}"
}

finish() {
  local rc=$?
  trap - EXIT

  if [[ "$rc" -ne 0 ]]; then
    echo >&2
    echo "FAILED at stage: $stage" >&2

    if [[ "$installed" -eq 1 && -n "$backup" && -f "$backup" ]]; then
      echo "Restoring previous v5..." >&2
      restore_tmp="$(mktemp "$dir/.bridge.py.restore.XXXXXX")" || restore_tmp=""
      if [[ -n "$restore_tmp" ]] && cp -p "$backup" "$restore_tmp" && mv -f "$restore_tmp" "$dst"; then
        restore_tmp=""
        launchctl kickstart -k "$label" >/dev/null 2>&1 || true
        echo "Previous v5 restored." >&2
      else
        echo "WARNING: automatic rollback failed." >&2
        echo "Rollback copy remains at: $backup" >&2
      fi
    fi

    [[ -z "$tmp" ]] || rm -f "$tmp"
    [[ -z "$restore_tmp" ]] || rm -f "$restore_tmp"
    echo "Your Terminal shell remains open." >&2
    exit "$rc"
  fi

  [[ -z "$tmp" ]] || rm -f "$tmp"
  [[ -z "$restore_tmp" ]] || rm -f "$restore_tmp"
  exit 0
}
trap finish EXIT

stage="preflight paths"
[[ -d "$repo" ]]
[[ -f "$src" ]]
[[ -f "$dst" ]]
[[ -x "$python" ]]
cd "$repo"

stage="Git/source identity"
[[ "$(git branch --show-current)" == "$expected_branch" ]]
[[ "$(git rev-parse HEAD:shell_bridge/bridge.py)" == "$expected_bridge_blob" ]]
[[ "$(git hash-object "$src")" == "$expected_bridge_blob" ]]
git diff --quiet -- shell_bridge/bridge.py

stage="source hash"
sha256_file "$src"
[[ "$REPLY" == "$expected_src_sha" ]]

stage="production identity"
sha256_file "$dst"
[[ "$REPLY" == "$expected_old_sha" ]]
old_mode="$(stat -f '%Lp' "$dst")"
old_owner="$(stat -f '%Su:%Sg' "$dst")"
[[ "$old_mode" == "700" ]]
[[ "$old_owner" == "tom:staff" ]]

stage="LaunchAgent identity"
service="$(launchctl print "$label")"
case "$service" in
  *"/opt/homebrew/bin/python3"*) ;;
  *) return 21 2>/dev/null || exit 21 ;;
esac
case "$service" in
  *"$dst"*) ;;
  *) return 22 2>/dev/null || exit 22 ;;
esac

stage="source validation"
"$python" -c 'import sys; p=sys.argv[1]; compile(open(p,"rb").read(),p,"exec")' "$src"
PYTHONPATH=. "$python" -m unittest shell_bridge.tests.test_bridge_legacy.ConfirmationTests.test_dialog_mode_requires_explicit_allow

if [[ "$mode" == "--dry-run" ]]; then
  stage="dry-run candidate"
  backup="$(mktemp "$repo/.v5-hotfix-dryrun-backup.XXXXXX")"
  tmp="$(mktemp "$repo/.v5-hotfix-dryrun-candidate.XXXXXX")"
else
  stage="backup"
  backup="$dir/bridge.py.rollback-v5-popup.$(date +%Y%m%d-%H%M%S).$$"
  [[ ! -e "$backup" ]]
  cp -p "$dst" "$backup"
  sha256_file "$backup"
  [[ "$REPLY" == "$expected_old_sha" ]]

  stage="candidate"
  tmp="$(mktemp "$dir/.bridge.py.v5repair.XXXXXX")"
fi

cp -p "$dst" "$tmp"
cat "$src" > "$tmp"
[[ "$(stat -f '%Lp' "$tmp")" == "$old_mode" ]]
[[ "$(stat -f '%Su:%Sg' "$tmp")" == "$old_owner" ]]
sha256_file "$tmp"
[[ "$REPLY" == "$expected_src_sha" ]]
"$python" -c 'import sys; p=sys.argv[1]; compile(open(p,"rb").read(),p,"exec")' "$tmp"
cmp -s "$src" "$tmp"

if [[ "$mode" == "--dry-run" ]]; then
  rm -f "$backup" "$tmp"
  backup=""
  tmp=""
  stage="complete"
  echo "DRYRUN_OK"
  exit 0
fi

stage="atomic install"
mv -f "$tmp" "$dst"
tmp=""
installed=1

stage="installed file verification"
sha256_file "$dst"
[[ "$REPLY" == "$expected_src_sha" ]]
[[ "$(stat -f '%Lp' "$dst")" == "$old_mode" ]]
[[ "$(stat -f '%Su:%Sg' "$dst")" == "$old_owner" ]]
"$python" -c 'import sys; p=sys.argv[1]; compile(open(p,"rb").read(),p,"exec")' "$dst"

stage="v5 restart"
launchctl kickstart -k "$label"
sleep 2

stage="post-restart LaunchAgent identity"
service="$(launchctl print "$label")"
case "$service" in
  *"/opt/homebrew/bin/python3"*) ;;
  *) exit 31 ;;
esac
case "$service" in
  *"$dst"*) ;;
  *) exit 32 ;;
esac

installed=0
stage="complete"
echo "v5 popup repair installed successfully."
echo "Installed SHA-256: $expected_src_sha"
echo "Rollback copy: $backup"
echo "Your Terminal shell remains open."
