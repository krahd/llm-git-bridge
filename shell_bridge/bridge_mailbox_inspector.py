#!/usr/bin/env python3
"""Read-only, redacted identity inventory for local bridge installations.

Run directly by a trusted local operator or through an explicitly installed
trusted-operation registration. It never prints arbitrary config keys, command
arguments, tokens, or executable content. It does not change service state.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

LABELS = {
    "io.llm-git-bridge.daemon": ".config/chatgpt-shell-bridge/config.json",
    "net.laurenzo.local-executor-bridge": ".config/local-executor-bridge/config.json",
    "net.laurenzo.local-executor-bridge-v6-manual-bootstrap":
        ".config/local-executor-bridge-v6-manual-bootstrap/config.json",
    "net.laurenzo.local-executor-bridge-v6-candidate-0b84b2f-g2":
        ".config/local-executor-bridge-v6-candidate-0b84b2f-g2/config.json",
}
FIELDS = ("bridge_instance_id", "drive_root_folder_id", "requests_folder_id",
          "results_folder_id", "state_dir", "base_path")


def inspect(home: Path, *, launchctl=None) -> dict:
    """Only fixed relative config paths; no user-controlled paths or command."""
    records = []
    launchctl = _launchctl if launchctl is None else launchctl
    for label, relative in LABELS.items():
        record = {"label": label}
        file = home / relative
        try:
            if any(node.is_symlink() for node in (file, *[p for p in file.parents if p != home and home in p.parents])):
                raise ValueError("config path traverses symlink")
            value = json.loads(file.read_text("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("config is not an object")
            for key in FIELDS:
                v = value.get(key)
                if isinstance(v, str) and len(v) <= 512 and not re.search(r"[\\x00-\\x1f\\x7f]", v):
                    record[key] = v
            record["config_status"] = "readable"
        except (OSError, ValueError, UnicodeError):
            record["config_status"] = "unreadable_or_invalid"
        record["service_state"] = launchctl(label)
        records.append(record)
    roots = {}
    for item in records:
        root = item.get("drive_root_folder_id")
        if root:
            roots.setdefault(root, []).append(item["label"])
    return {
        "schema": 1,
        "kind": "bridge_mailbox_ownership_inventory",
        "records": records,
        "shared_drive_roots": {key: labels for key, labels in roots.items()
                               if len(labels) > 1},
        "mutations_performed": False,
    }


def _launchctl(label: str) -> str:
    try:
        p = subprocess.run(
            ["/bin/launchctl", "list", label],
            capture_output=True, timeout=4, check=False,
        )
        return "loaded" if p.returncode == 0 else "not_loaded"
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"


def main() -> int:
    import os
    home = Path.home()
    # Direct operator invocation takes no arguments; the pinned bridge
    # dispatcher passes the fixed registered action and the user's home root.
    argv = __import__("sys").argv[1:]
    if argv not in ([], ["inspect", str(home)]):
        raise SystemExit("invalid registered mailbox-inspection invocation")
    print(json.dumps(inspect(home), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
