#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
EXTENSION_DIR="$SCRIPT_DIR/extension"
OUTPUT_DIR=${1:-"$SCRIPT_DIR/build"}

command -v xcrun >/dev/null 2>&1 || {
  echo "safari/package.sh requires Xcode command-line tools" >&2
  exit 1
}

mkdir -p "$OUTPUT_DIR"

xcrun safari-web-extension-packager \
  --project-location "$OUTPUT_DIR" \
  --app-name "Conversation Harness" \
  --bundle-identifier "net.laurenzo.chatgpt-conversation-harness" \
  --swift \
  --macos-only \
  --copy-resources \
  --no-open \
  --no-prompt \
  --force \
  "$EXTENSION_DIR"
