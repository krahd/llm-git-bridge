#!/bin/sh
set -eu

APP_NAME=llm-git-bridge
REPO_URL=${LLM_GIT_BRIDGE_REPO_URL:-https://github.com/krahd/llm-git-bridge.git}
REF=${LLM_GIT_BRIDGE_REF:-main}
INSTALL_DIR=${LLM_GIT_BRIDGE_INSTALL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/llm-git-bridge}
BIN_DIR=${LLM_GIT_BRIDGE_BIN_DIR:-$HOME/.local/bin}
RUN_SETUP=1
RUN_DAEMON=1

usage() {
  cat <<'USAGE'
Usage: install.sh [--no-setup] [--no-daemon]

Installs or safely updates LLM Git Bridge, then runs the same interactive
setup wizard used for later reconfiguration. Repository folders are never
hardcoded by this installer; they are chosen in the setup wizard.

Environment overrides:
  LLM_GIT_BRIDGE_REPO_URL     Git repository URL
  LLM_GIT_BRIDGE_REF          Branch or tag to install (default: main)
  LLM_GIT_BRIDGE_INSTALL_DIR  Source checkout directory
  LLM_GIT_BRIDGE_BIN_DIR      Directory for the llm-git-bridge command
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-setup) RUN_SETUP=0 ;;
    --no-daemon) RUN_DAEMON=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

say() { printf '%s\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

confirm() {
  prompt=$1
  default=${2:-yes}
  if [ ! -t 0 ]; then
    [ "$default" = yes ]
    return
  fi
  if [ "$default" = yes ]; then
    suffix='[Y/n]'
  else
    suffix='[y/N]'
  fi
  printf '%s %s ' "$prompt" "$suffix"
  IFS= read -r answer || answer=''
  case "$answer" in
    '') [ "$default" = yes ] ;;
    y|Y|yes|YES|Yes) return 0 ;;
    *) return 1 ;;
  esac
}

command -v git >/dev/null 2>&1 || fail "git is required"
PYTHON=$(command -v python3 || true)
[ -n "$PYTHON" ] || fail "Python 3.11 or newer is required"
"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
  || fail "Python 3.11 or newer is required"

OS=$(uname -s 2>/dev/null || printf unknown)
if ! command -v rclone >/dev/null 2>&1; then
  if [ "$OS" = Darwin ] && command -v brew >/dev/null 2>&1 \
      && confirm "rclone is required. Install it with Homebrew now?" yes; then
    brew install rclone
  else
    fail "rclone is required. Install it from https://rclone.org/install/ and rerun this installer"
  fi
fi

say "LLM Git Bridge"
say "Install directory: $INSTALL_DIR"

if [ -d "$INSTALL_DIR/.git" ]; then
  if [ -n "$(git -C "$INSTALL_DIR" status --porcelain)" ]; then
    fail "the existing installation has local changes; commit or remove them before updating"
  fi
  say "Updating existing installation..."
  git -C "$INSTALL_DIR" fetch --prune origin "$REF"
  if git -C "$INSTALL_DIR" show-ref --verify --quiet "refs/heads/$REF"; then
    git -C "$INSTALL_DIR" checkout -q "$REF"
    git -C "$INSTALL_DIR" merge --ff-only "origin/$REF"
  else
    git -C "$INSTALL_DIR" checkout -q --track -b "$REF" "origin/$REF"
  fi
elif [ -e "$INSTALL_DIR" ]; then
  if [ -d "$INSTALL_DIR" ] && [ -z "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
    rmdir "$INSTALL_DIR"
    git clone --branch "$REF" --single-branch "$REPO_URL" "$INSTALL_DIR"
  else
    fail "$INSTALL_DIR exists but is not an llm-git-bridge Git checkout"
  fi
else
  mkdir -p "$(dirname -- "$INSTALL_DIR")"
  git clone --branch "$REF" --single-branch "$REPO_URL" "$INSTALL_DIR"
fi

mkdir -p "$BIN_DIR"
ln -sfn "$INSTALL_DIR/bin/llm-git-bridge" "$BIN_DIR/llm-git-bridge"
BRIDGE=$BIN_DIR/llm-git-bridge

if [ "$RUN_SETUP" -eq 1 ]; then
  if [ -z "$(rclone listremotes 2>/dev/null || true)" ]; then
    if confirm "No rclone remotes are configured. Open rclone configuration now?" yes; then
      rclone config
    else
      say "No rclone remote configured. Setup may ask for one; you can rerun this installer later."
    fi
  fi
  "$BRIDGE" setup
fi

if [ "$OS" = Darwin ] && [ "$RUN_DAEMON" -eq 1 ]; then
  if confirm "Start LLM Git Bridge automatically when you log in?" yes; then
    "$BRIDGE" daemon install --command "$BRIDGE"
  fi
elif [ "$OS" != Darwin ]; then
  say "Automatic service installation is currently macOS-only."
  say "Run '$BRIDGE watch' with your operating system's process supervisor."
fi

say ""
say "LLM Git Bridge is installed."
say "Command: $BRIDGE"
say "Run '$BRIDGE setup' at any time to reconfigure repository roots and permissions."
