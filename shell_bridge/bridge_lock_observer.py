#!/usr/bin/env python3
"""Read-only OS evidence about bridge instance-lock files.

An open lock-file descriptor is NOT proof of an exclusive flock; never use this
report alone to authorize shutdown, upgrade, or v5 retirement.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from bridge_service_ownership import inspect_services

FOLDER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
PID = re.compile(r"p([0-9]+)\Z")


def lock_path(mailbox_id: str, *, uid: int | None = None,
              tmp: Path | None = None) -> Path:
    if not isinstance(mailbox_id, str) or not FOLDER.fullmatch(mailbox_id):
        raise ValueError("invalid mailbox ID")
    digest = hashlib.sha256(("drive-mailbox:" + mailbox_id).encode("utf-8")).hexdigest()[:32]
    base = Path(tempfile.gettempdir()) if tmp is None else tmp
    return base / ("chatgpt-shell-bridge-" + str(os.getuid() if uid is None else uid)) / (digest + ".lock")


def open_lock_file_pids(path: Path, *, runner=subprocess.run) -> dict:
    """Inspect existing lock file without opening or locking it ourselves."""
    if path.is_symlink():
        return {"state": "unverified", "pids": [], "reason": "lock path is a symlink"}
    if not path.is_file():
        return {"state": "not_present", "pids": []}
    try:
        result = runner(["/usr/sbin/lsof", "-w", "-F", "p", str(path)],
                        capture_output=True, text=True, timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {"state": "unverified", "pids": [], "reason": "cannot inspect open descriptors"}
    if result.returncode != 0 or result.stderr.strip():
        return {"state": "unverified", "pids": [], "reason": "lsof did not return verified evidence"}
    rows = result.stdout.splitlines()
    pids = set()
    for row in rows:
        m = PID.fullmatch(row)
        if not m:
            return {"state": "unverified", "pids": [], "reason": "unexpected lsof output"}
        pids.add(m.group(1))
    return {"state": "open_descriptors_observed" if pids else "no_open_descriptors_observed",
            "pids": sorted(pids, key=int)}


def inspect(root: dict, *, runner=subprocess.run) -> dict:
    if not isinstance(root, dict) or root.get("kind") != "loaded_bridge_ownership":
        raise ValueError("invalid loaded-owner inventory")
    by_mailbox: dict[str, list[dict]] = {}
    for record in root.get("loaded", []):
        mailbox = record.get("drive_root_folder_id")
        if mailbox is not None:
            by_mailbox.setdefault(mailbox, []).append(record)
    lock_evidence = []
    for mailbox, claimants in sorted(by_mailbox.items()):
        path = lock_path(mailbox)
        data = open_lock_file_pids(path, runner=runner)
        claimant_pids = {x["pid"] for x in claimants if x.get("pid")}
        evidence = {
            "mailbox": mailbox, "lock_evidence_state": data["state"],
            "open_lock_file_pids": data["pids"],
            "claimant_pids": sorted(claimant_pids, key=int),
            "unrecognized_open_pids": [p for p in data["pids"] if p not in claimant_pids],
        }
        if data.get("reason"):
            evidence["reason"] = data["reason"]
        lock_evidence.append(evidence)
    return {
        "schema": 1, "kind": "bridge_open_lock_file_evidence",
        "lock_evidence": lock_evidence,
        "source_safe_to_stage": root.get("safe_to_stage") is True,
        "exclusive_lock_proved": False,
        "retirement_authorized": False,
        "mutations_performed": False,
    }


def main() -> int:
    try:
        launchd = subprocess.run(["/bin/launchctl", "list"], capture_output=True,
                                 text=True, timeout=8, check=False)
        process = subprocess.run(["/bin/ps", "-axo", "pid=,command="],
                                 capture_output=True, text=True,
                                 timeout=8, check=False)
        if launchd.returncode != 0 or process.returncode != 0:
            raise ValueError("unable to obtain process/LaunchAgent inventory")
        root = inspect_services(Path.home(), launchd.stdout, process.stdout)
        report = inspect(root)
        print(json.dumps(report, sort_keys=True))
        return 0 if root["safe_to_stage"] else 3
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print("LOCK_EVIDENCE_UNVERIFIED: " + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
