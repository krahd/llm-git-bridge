#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SUFFIX="${1:-candidate-$(git -C "$ROOT" rev-parse --short=12 HEAD)}"
OUT="${2:-$ROOT/shell_bridge/.migrator-app/Local Executor Bridge v6 Migrator.app}"
rm -rf "$OUT"
mkdir -p "$OUT/Contents/MacOS"
python3 - "$OUT/Contents/Info.plist" <<'PYP'
import plistlib,sys
obj={'CFBundleIdentifier':'net.laurenzo.local-executor-bridge-v6-migrator','CFBundleName':'Local Executor Bridge v6 Migrator','CFBundleDisplayName':'Local Executor Bridge v6 Migrator','CFBundlePackageType':'APPL','CFBundleExecutable':'migrate','CFBundleVersion':'1','CFBundleShortVersionString':'1.0','LSUIElement':True}
with open(sys.argv[1],'wb') as f: plistlib.dump(obj,f,sort_keys=True)
PYP
cat > "$OUT/Contents/MacOS/migrate" <<EOF
#!/bin/bash
exec /bin/bash "$ROOT/shell_bridge/migrate_v6_staging_atomic.sh" "$SUFFIX"
EOF
chmod 700 "$OUT/Contents/MacOS/migrate"
printf '%s\n' "$OUT"
