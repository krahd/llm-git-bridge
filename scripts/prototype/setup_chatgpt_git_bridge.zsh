#!/bin/zsh
set -euo pipefail

REPO_PATH="${CHATGPT_GIT_BRIDGE_REPO:-$HOME/path/to/repository}"
PYTHON_BIN="${CHATGPT_GIT_BRIDGE_PYTHON:-/opt/homebrew/bin/python3}"
REMOTE_NAME="${CHATGPT_GIT_BRIDGE_REMOTE:-chatgpt-git-bridge}"
BRIDGE_FOLDER_ID="${CHATGPT_GIT_BRIDGE_FOLDER_ID:-}"
EXPECTED_GOOGLE_ACCOUNT="${CHATGPT_GIT_BRIDGE_GOOGLE_ACCOUNT:-}"
CONFIG_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}/chatgpt-git-bridge"
STATE_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/chatgpt-git-bridge"
MAX_TEXT_FILE_BYTES="${CHATGPT_GIT_BRIDGE_MAX_TEXT_FILE_BYTES:-2000000}"

log() { print -r -- "[bridge-setup] $*"; }
warn() { print -r -- "[bridge-setup] WARNING: $*" >&2; }
die() { print -r -- "[bridge-setup] ERROR: $*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
remote_exists() { rclone listremotes 2>/dev/null | grep -Fxq "${REMOTE_NAME}:"; }
confirm() {
  local prompt="$1" answer=""
  read "answer?${prompt} [y/N] "
  [[ "${answer:l}" == "y" || "${answer:l}" == "yes" ]]
}

log "Starting ChatGPT Git bridge setup."
log "Repository: $REPO_PATH"
log "Drive remote: $REMOTE_NAME"
log "Expected Google account: $EXPECTED_GOOGLE_ACCOUNT"
print

[[ "$(uname -s)" == "Darwin" ]] || die "This bootstrap script currently supports macOS only."
[[ -n "$BRIDGE_FOLDER_ID" ]] || die "Set CHATGPT_GIT_BRIDGE_FOLDER_ID before using this historical prototype."
[[ -d "$REPO_PATH" ]] || die "Repository directory does not exist: $REPO_PATH"
git -C "$REPO_PATH" rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "Not a Git working tree: $REPO_PATH"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi
[[ -n "$PYTHON_BIN" && -x "$PYTHON_BIN" ]] || die "python3 was not found."
log "Python: $("$PYTHON_BIN" --version 2>&1) ($PYTHON_BIN)"

if ! have brew; then
  if [[ -x /opt/homebrew/bin/brew ]]; then
    export PATH="/opt/homebrew/bin:$PATH"
  else
    die "Homebrew is required to install rclone. Install Homebrew first, then rerun this script."
  fi
fi

if ! have rclone; then
  log "rclone is not installed."
  if confirm "Install rclone with Homebrew now?"; then
    brew install rclone
  else
    die "rclone is required."
  fi
fi

log "rclone: $(rclone version | head -n 1)"

if ! remote_exists; then
  cat <<EOT

One interactive OAuth step is required.

The script will now open rclone's configuration UI. Create exactly one remote
with these settings:

  New remote:                n
  name:                      ${REMOTE_NAME}
  storage type:              drive   (Google Drive)
  scope:                     drive   (full Drive scope)
  service account file:      leave blank
  browser authentication:    yes
  Google account:            ${EXPECTED_GOOGLE_ACCOUNT}
  Shared Drive / Team Drive: no
  keep/save remote:          yes

IMPORTANT:
  We need the "drive" scope, not "drive.file". Files written by ChatGPT's
  Google Drive connector were not created by rclone, so drive.file would make
  those transaction files invisible to the local bridge.

  rclone's shared Google OAuth client is being retired during 2026. If rclone
  offers it and it still works, it is sufficient for this test. If Google or
  rclone rejects it, stop rather than entering random credentials; we can then
  configure a dedicated OAuth client for the packaged wrapper.

After OAuth, this script will set rclone's operational root to the existing
ChatGPT-Git-Bridge folder automatically.

Press Return to open rclone config.
EOT
  read -r _
  rclone config
  remote_exists || die "Remote '${REMOTE_NAME}:' was not created. Rerun and create it with that exact name."
else
  log "Existing rclone remote '${REMOTE_NAME}:' found."
fi

log "Setting the remote root to the existing ChatGPT-Git-Bridge folder."
rclone config update "$REMOTE_NAME" root_folder_id "$BRIDGE_FOLDER_ID" >/dev/null

log "Checking access to the bridge folder..."
if ! rclone lsf "${REMOTE_NAME}:" --max-depth 1 >/dev/null 2>&1; then
  warn "The remote exists but cannot read the ChatGPT-Git-Bridge folder."
  warn "This usually means OAuth used the wrong Google account, the token needs reconnecting,"
  warn "or the account cannot access Drive folder id $BRIDGE_FOLDER_ID."
  print
  warn "Try: rclone config reconnect ${REMOTE_NAME}:"
  exit 1
fi
log "Drive access works."

RCLONE_CONFIG_FILE="$(rclone config file 2>/dev/null | tail -n 1 || true)"
if [[ -n "$RCLONE_CONFIG_FILE" && -f "$RCLONE_CONFIG_FILE" ]]; then
  chmod 600 "$RCLONE_CONFIG_FILE" || true
  log "rclone OAuth config: $RCLONE_CONFIG_FILE (permissions set to 600)"
fi

REPO_NAME="$(basename "$REPO_PATH")"
LOCAL_REPO_CONFIG_DIR="$CONFIG_ROOT/repos"
LOCAL_REPO_STATE_DIR="$STATE_ROOT/$REPO_NAME"
mkdir -p "$LOCAL_REPO_CONFIG_DIR" "$LOCAL_REPO_STATE_DIR"

REMOTE_REPO_DIR="repos/$REPO_NAME"
REMOTE_TRANSACTIONS_DIR="transactions"
REMOTE_RESULTS_DIR="results"

log "Creating bridge directories in Drive if needed."
rclone mkdir "${REMOTE_NAME}:${REMOTE_REPO_DIR}"
rclone mkdir "${REMOTE_NAME}:${REMOTE_TRANSACTIONS_DIR}"
rclone mkdir "${REMOTE_NAME}:${REMOTE_RESULTS_DIR}"

SNAPSHOT_FILE="$LOCAL_REPO_STATE_DIR/snapshot.txt"
STATE_FILE="$LOCAL_REPO_STATE_DIR/state.json"
LOCAL_CONFIG_FILE="$LOCAL_REPO_CONFIG_DIR/$REPO_NAME.json"

log "Generating repository snapshot from tracked files at the current HEAD."

"$PYTHON_BIN" - "$REPO_PATH" "$SNAPSHOT_FILE" "$STATE_FILE" "$LOCAL_CONFIG_FILE" "$REMOTE_NAME" "$REMOTE_REPO_DIR" "$MAX_TEXT_FILE_BYTES" <<'PY'
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import sys

repo = Path(sys.argv[1]).resolve()
snapshot_path = Path(sys.argv[2])
state_path = Path(sys.argv[3])
config_path = Path(sys.argv[4])
remote_name = sys.argv[5]
remote_repo_dir = sys.argv[6]
max_bytes = int(sys.argv[7])

def git(*args: str, binary: bool = False):
    p = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return p.stdout if binary else p.stdout.decode("utf-8", errors="strict").rstrip("\n")

head = git("rev-parse", "HEAD")
branch = git("branch", "--show-current") or "(detached)"
status = git("status", "--porcelain=v1")
dirty = bool(status.strip())
raw_names = git("ls-files", "-z", binary=True)
paths = [p.decode("utf-8", errors="surrogateescape") for p in raw_names.split(b"\0") if p]
generated_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

included = []
omitted = []
snapshot_path.parent.mkdir(parents=True, exist_ok=True)

with snapshot_path.open("w", encoding="utf-8", newline="\n") as out:
    out.write("BRIDGE-SNAPSHOT v1\n")
    out.write(f"repo: {repo.name}\n")
    out.write(f"base_sha: {head}\n")
    out.write(f"branch: {branch}\n")
    out.write(f"generated_at: {generated_at}\n")
    out.write(f"working_tree_dirty: {str(dirty).lower()}\n")
    out.write(f"tracked_files: {len(paths)}\n\n")

    for rel in paths:
        path = repo / rel
        try:
            data = path.read_bytes()
        except OSError as e:
            omitted.append({"path": rel, "reason": f"read_error:{e.__class__.__name__}"})
            continue

        sha = hashlib.sha256(data).hexdigest()
        if len(data) > max_bytes:
            omitted.append({"path": rel, "reason": "too_large", "bytes": len(data), "sha256": sha})
            continue
        if b"\x00" in data[:8192]:
            omitted.append({"path": rel, "reason": "binary", "bytes": len(data), "sha256": sha})
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            omitted.append({"path": rel, "reason": "non_utf8", "bytes": len(data), "sha256": sha})
            continue

        included.append({"path": rel, "bytes": len(data), "sha256": sha})
        out.write(f"===== FILE: {rel} =====\n")
        out.write(f"sha256: {sha}\n")
        out.write(f"bytes: {len(data)}\n")
        out.write("----- CONTENT -----\n")
        out.write(text)
        if text and not text.endswith("\n"):
            out.write("\n")
        out.write(f"===== END FILE: {rel} =====\n\n")

    if omitted:
        out.write("===== OMITTED FILES =====\n")
        for entry in omitted:
            out.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        out.write("===== END OMITTED FILES =====\n")

snapshot_sha = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
state = {
    "protocol": 1,
    "repo": repo.name,
    "base_sha": head,
    "branch": branch,
    "working_tree_dirty": dirty,
    "generated_at": generated_at,
    "snapshot_file": "snapshot.txt",
    "snapshot_sha256": snapshot_sha,
    "tracked_files": len(paths),
    "included_text_files": len(included),
    "omitted_files": len(omitted),
}
config = {
    "protocol": 1,
    "repo": repo.name,
    "repo_path": str(repo),
    "rclone_remote": remote_name,
    "remote_repo_dir": remote_repo_dir,
    "allowed_branch_prefix": "ai/",
    "allow_commit": True,
    "allow_push": False,
    "allow_pr": False,
}
state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(state, indent=2, sort_keys=True))
PY

log "Uploading state.json and snapshot.txt to Drive."
rclone copyto "$STATE_FILE" "${REMOTE_NAME}:${REMOTE_REPO_DIR}/state.json"
rclone copyto "$SNAPSHOT_FILE" "${REMOTE_NAME}:${REMOTE_REPO_DIR}/snapshot.txt"

log "Verifying uploaded files."
REMOTE_STATE="$(rclone cat "${REMOTE_NAME}:${REMOTE_REPO_DIR}/state.json")"
[[ -n "$REMOTE_STATE" ]] || die "Uploaded state.json could not be read back."
REMOTE_SNAPSHOT_HEAD="$(rclone cat "${REMOTE_NAME}:${REMOTE_REPO_DIR}/snapshot.txt" | head -n 8)"
[[ "$REMOTE_SNAPSHOT_HEAD" == *"BRIDGE-SNAPSHOT v1"* ]] || die "Uploaded snapshot did not verify."

print
log "Setup complete."
print
print -r -- "Local repository:"
print -r -- "  $REPO_PATH"
print -r -- ""
print -r -- "Local bridge config:"
print -r -- "  $LOCAL_CONFIG_FILE"
print -r -- ""
print -r -- "Drive bridge root:"
print -r -- "  Google Drive folder id: $BRIDGE_FOLDER_ID"
print -r -- "  rclone remote: ${REMOTE_NAME}:"
print -r -- ""
print -r -- "Uploaded:"
print -r -- "  ${REMOTE_NAME}:${REMOTE_REPO_DIR}/state.json"
print -r -- "  ${REMOTE_NAME}:${REMOTE_REPO_DIR}/snapshot.txt"
print -r -- ""
print -r -- "Safety defaults:"
print -r -- "  commits on ai/* branches: allowed by future bridge daemon"
print -r -- "  push: disabled"
print -r -- "  pull-request creation: disabled"
print
print -r -- "You can now tell ChatGPT:"
print -r -- '  "Read the bridge-test repository from ChatGPT-Git-Bridge/repos/bridge-test."'
print
print -r -- "This setup script has not modified, committed, or pushed anything in the Git repository."
