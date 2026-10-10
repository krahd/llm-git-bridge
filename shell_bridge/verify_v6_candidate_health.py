#!/usr/bin/env python3
"""Verify that a newly staged v6 daemon is actually live, and is the intended build.

This is a strict local acceptance preflight, not approval/replay acceptance.
No state changes and no secrets are printed.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path


class HealthVerificationError(Exception):
    pass


def verify(config: dict, health: dict, source_commit: str, label: str,
           launchctl_output: str, now: dt.datetime | None = None) -> int:
    expected = {
        "bridge_instance_id": config.get("bridge_instance_id"),
        "drive_root_folder_id": config.get("drive_root_folder_id"),
        "requests_folder_id": config.get("requests_folder_id"),
        "results_folder_id": config.get("results_folder_id"),
        "build_source_commit": source_commit,
        "bridge_version": "6",
        "build_code_integrity": "matches_manifest",
    }
    if len(source_commit) != 40 or any(c not in "0123456789abcdef" for c in source_commit):
        raise HealthVerificationError("invalid source revision")
    for key, value in expected.items():
        if not isinstance(value, str) or not value or health.get(key) != value:
            raise HealthVerificationError("candidate identity/integrity mismatch: " + key)
    pid = health.get("publisher_pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise HealthVerificationError("candidate health contains no valid daemon PID")
    # launchctl list LABEL emits the loaded service PID in its dictionary.
    match = re.search(r'"PID"\s*=\s*(\d+)\s*;', launchctl_output)
    if match is None or int(match.group(1)) != pid:
        raise HealthVerificationError("candidate health PID is not the live launchd service")
    try:
        timestamp = dt.datetime.fromisoformat(
            health["updated_at"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError("missing timezone")
        instant = now or dt.datetime.now(dt.timezone.utc)
        age = (instant - timestamp).total_seconds()
    except (KeyError, ValueError, TypeError, AttributeError):
        raise HealthVerificationError("invalid heartbeat timestamp")
    if not -30 <= age <= 180:
        raise HealthVerificationError("candidate heartbeat is stale or from the future")
    return pid


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print("usage: verify_v6_candidate_health.py CONFIG HEALTH SHA LABEL", file=sys.stderr)
        return 2
    config_path, health_path, source_commit, label = argv[1:]
    try:
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
        health = json.loads(Path(health_path).read_text(encoding="utf-8"))
        launchctl = subprocess.run(
            ["/bin/launchctl", "list", label], capture_output=True,
            text=True, check=False, timeout=5,
        )
        if launchctl.returncode != 0:
            raise HealthVerificationError("candidate service is not loaded")
        pid = verify(config, health, source_commit, label, launchctl.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired,
            HealthVerificationError) as exc:
        # Don't dump the config, tokens, or raw CLI output on failure.
        print("CANDIDATE_NOT_VERIFIED: " + str(exc), file=sys.stderr)
        return 1
    print("ISOLATED_V6_HEALTH_VERIFIED=1 PID=" + str(pid))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
