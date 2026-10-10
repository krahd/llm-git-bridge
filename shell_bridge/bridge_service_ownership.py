#!/usr/bin/env python3
"""Fail-closed live LaunchAgent mailbox ownership inventory.

Only inspects loaded launchd jobs with bridge-related labels. A fixed list of
historical configs is insufficient because new candidate labels are dynamic.
Never prints arbitrary config fields, program arguments, or credentials.
"""
from __future__ import annotations

import json
import plistlib
import subprocess
import sys
from pathlib import Path

IDENTITY = ("bridge_instance_id", "drive_root_folder_id",
            "requests_folder_id", "results_folder_id", "state_dir")
KEYWORDS = ("bridge", "local-executor")


def _safe_child(path: Path, home: Path) -> bool:
    if not path.is_absolute() or home not in path.parents:
        return False
    current = path
    while current != home:
        if current.is_symlink():
            return False
        current = current.parent
    return True


def inspect_services(home: Path, launchctl_listing: str) -> dict:
    """Read only fixed user's LaunchAgent plists and their declared config paths."""
    home = home.absolute()
    records = []
    problems = []
    for line in launchctl_listing.splitlines()[1:]:
        fields = line.split(None, 2)
        if len(fields) != 3:
            continue
        pid, status, label = fields
        if not any(word in label.lower() for word in KEYWORDS):
            continue
        # A loaded KeepAlive service without a PID may launch at any time.
        record = {"label": label, "pid": pid if pid.isdecimal() else None,
                  "service_status": status}
        plist = home / "Library" / "LaunchAgents" / (label + ".plist")
        try:
            if not _safe_child(plist, home) or not plist.is_file():
                raise ValueError("no safe LaunchAgent plist")
            with plist.open("rb") as stream:
                item = plistlib.load(stream)
            argv = item.get("ProgramArguments")
            if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
                raise ValueError("missing program arguments")
            args = [x for x in argv if x.startswith("--config=")]
            positions = [i for i, x in enumerate(argv) if x == "--config"]
            if len(args) + len(positions) != 1:
                raise ValueError("config argument is missing or ambiguous")
            if args:
                config = Path(args[0].split("=", 1)[1])
            else:
                index = positions[0]
                if index + 1 >= len(argv):
                    raise ValueError("config argument has no value")
                config = Path(argv[index + 1])
            if (not _safe_child(config, home)
                    or config.parent.parent != home / ".config"
                    or not config.is_file()):
                raise ValueError("config must be a safe direct ~/.config/<bridge>/config.json file")
            with config.open("r", encoding="utf-8") as stream:
                value = json.load(stream)
            if not isinstance(value, dict):
                raise ValueError("config not an object")
            for key in IDENTITY:
                field = value.get(key)
                if not isinstance(field, str) or not 0 < len(field) <= 512 or not field.isprintable():
                    raise ValueError("missing or unsafe identity field")
                if key == "state_dir":
                    location = Path(field).expanduser()
                    if not location.is_absolute():
                        raise ValueError("state directory must be absolute")
                    # Canonicalise alias paths before comparing shared journals.
                    # A pair of distinct mailbox IDs sharing durable state is
                    # just as unsafe as duplicate mailbox consumers.
                    field = str(location.resolve(strict=False))
                record[key] = field
            record["state"] = "verified"
        except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
            record["state"] = "unverifiable"
            problems.append("unverifiable loaded bridge: " + label)
        records.append(record)
    for field in ("drive_root_folder_id", "requests_folder_id", "results_folder_id",
                  "state_dir"):
        values = {}
        for record in records:
            val = record.get(field)
            if val is not None:
                values.setdefault(val, []).append(record["label"])
        for labels in values.values():
            if len(labels) > 1:
                problems.append("shared " + field + " between " + ", ".join(sorted(labels)))
    return {"schema": 1, "kind": "loaded_bridge_ownership",
            "loaded": records, "problems": sorted(set(problems)),
            "safe_to_stage": not problems, "mutations_performed": False}


def main() -> int:
    try:
        output = subprocess.run(["/bin/launchctl", "list"], capture_output=True,
                                text=True, timeout=8, check=False)
        if output.returncode != 0:
            raise ValueError("launchctl could not enumerate loaded services")
        inventory = inspect_services(Path.home(), output.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        print("BRIDGE_OWNERSHIP_UNVERIFIED: launchd inventory unavailable", file=sys.stderr)
        return 2
    print(json.dumps(inventory, sort_keys=True))
    return 0 if inventory["safe_to_stage"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
