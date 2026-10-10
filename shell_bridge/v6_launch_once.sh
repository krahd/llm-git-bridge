#!/bin/bash
# Local one-invocation launcher for a dirty Mac checkout. It never resets,
# cleans, checks out, stashes or changes the operator's existing worktree.
set -euo pipefail
hold(){ printf 'HOLD: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = Darwin ] || {
  [ "${V6_RELEASE_DRY_RUN:-0}" = 1 ] || hold "this launcher requires macOS"
}
[ -z "${CHATGPT_SHELL_BRIDGE_REQUEST_ID:-}" ] ||
  hold "launch only from a local Terminal, not the bridge being replaced"
[ "$#" -eq 1 ] || hold "usage: v6_launch_once.sh ABSOLUTE_LOCAL_REPO_PATH"
[ -d "$1" ] || hold "local bridge repository not found"

REPO="$(git -C "$1" rev-parse --show-toplevel)" || hold "not a Git checkout"
REPO="$(cd "$REPO" && pwd -P)"
ORIGIN="$(git -C "$REPO" remote get-url origin)" || hold "missing canonical origin"
case "$ORIGIN" in
  git@github.com:krahd/llm-git-bridge|git@github.com:krahd/llm-git-bridge.git|\
  https://github.com/krahd/llm-git-bridge|https://github.com/krahd/llm-git-bridge.git)
    ;;
  *)
    [ "${V6_RELEASE_DRY_RUN:-0}" = 1 ] ||
      hold "origin is not the expected canonical GitHub bridge repository"
    ;;
esac
if [ "${V6_RELEASE_DRY_RUN:-0}" != 1 ]; then
  [ -t 0 ] || hold "native approval and cutover require an interactive local Terminal"
fi
# Updates only the remote-tracking ref; preserves dirty worktree and HEAD.
git -C "$REPO" fetch origin refs/heads/main:refs/remotes/origin/main ||
  hold "canonical main fetch failed; preserve the local checkout"
SOURCE="$(git -C "$REPO" rev-parse --verify refs/remotes/origin/main^{commit})" ||
  hold "canonical origin/main could not be verified"
case "$SOURCE" in
  *[!0-9a-f]*|'') hold "invalid canonical source SHA" ;;
esac
[ "${#SOURCE}" -eq 40 ] || hold "invalid canonical source SHA"

BASE="$HOME/.local/state/bridge-v6-release-sources"
# Source worktree must remain outside any agent-writable repository root.
# Resolve existing symlinked ancestors before creating anything.
python3 - "$BASE" "${ALLOWED_ROOT:-$HOME/tom-repos}" <<'PYSAFEPATH' ||
  hold "release source is within an agent-writable or unsafe path"
import os,sys
from pathlib import Path
base=Path(sys.argv[1]).resolve()
allowed=Path(sys.argv[2]).expanduser().resolve()
if base==allowed or allowed in base.parents:
    raise SystemExit("HOLD: release source cannot be agent writable")
home=Path.home().resolve()
if base==home or home not in base.parents:
    raise SystemExit("HOLD: release source must stay in protected home state")
PYSAFEPATH
[ ! -L "$BASE" ] || hold "release source directory is a symlink"
mkdir -p -m 700 "$BASE"
chmod 700 "$BASE" || hold "cannot protect release source directory"
[ -d "$BASE" ] && [ ! -L "$BASE" ] || hold "unsafe release source directory"
DEST="$BASE/$SOURCE"
[ ! -e "$DEST" ] && [ ! -L "$DEST" ] ||
  hold "this exact release worktree already exists; reconcile rather than rerun"
git -C "$REPO" worktree add --detach "$DEST" "$SOURCE" ||
  hold "worktree creation was partial or failed; inspect before any retry"
[ "$(git -C "$DEST" rev-parse HEAD)" = "$SOURCE" ] ||
  hold "detached release source HEAD differs from fetched main"
[ -z "$(git -C "$DEST" status --porcelain)" ] ||
  hold "new release worktree is not clean"
[ "$(git -C "$DEST" rev-parse refs/remotes/origin/main)" = "$SOURCE" ] ||
  hold "fetched source no longer equals origin/main"
[ -f "$DEST/shell_bridge/v6_release_once.sh" ] ||
  hold "qualified release entrypoint is absent"
printf 'EXACT_CANONICAL_RELEASE_SOURCE=%s\n' "$SOURCE"
printf 'ISOLATED_RELEASE_WORKTREE=%s\n' "$DEST"
printf 'ORIGINAL_CHECKOUT_PRESERVED=%s\n' "$REPO"
if [ "${V6_RELEASE_DRY_RUN:-0}" = 1 ]; then
  printf 'V6_RELEASE_SOURCE_PREPARED_ONLY=1\n'
  exit 0
fi
cd "$DEST"
exec /bin/bash shell_bridge/v6_release_once.sh
