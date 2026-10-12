#!/usr/bin/env python3
"""Fail-closed read-only preflight for a v5-to-v6 production migration.

A live v6 heartbeat on the pinned production mailbox is NOT evidence that
v5 is still the sole owner. Do not run the v5 retirement controller on it.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path

PRODUCTION_ROOT = "1PRsQHgVsgXhIYT_8akGFRGdhdlw9Lmk6"


class ProductionIdentityError(ValueError):
    pass


def load_legacy_config(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ProductionIdentityError("canonical legacy config missing or symlinked")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ProductionIdentityError("canonical legacy config cannot be read") from exc
    if not isinstance(config, dict) or config.get("drive_root_folder_id") != PRODUCTION_ROOT:
        raise ProductionIdentityError("legacy config does not pin the production root")
    remote = config.get("remote")
    if not isinstance(remote, str) or not remote.endswith(":") or len(remote) > 128:
        raise ProductionIdentityError("legacy rclone remote is invalid")
    ident = config.get("bridge_instance_id")
    if not isinstance(ident, str) or not ident:
        raise ProductionIdentityError("legacy bridge instance identity is absent")
    return config


def verify_v5_production_health(config: dict, health: dict, *, now: float | None = None) -> None:
    if not isinstance(health, dict) or health.get("kind") != "shell_bridge_health":
        raise ProductionIdentityError("production health record is not a bridge heartbeat")
    version = health.get("bridge_version")
    if isinstance(version, bool) or str(version) != "5":
        raise ProductionIdentityError(
            "production mailbox already reports v6 or unknown bridge version; "
            "reconcile actual loaded owners before v5-to-v6 migration"
        )
    if health.get("drive_root_folder_id") != PRODUCTION_ROOT:
        raise ProductionIdentityError("production heartbeat is attached to a different mailbox")
    if health.get("bridge_instance_id") != config["bridge_instance_id"]:
        raise ProductionIdentityError("production heartbeat and legacy config disagree on instance")
    timestamp = health.get("updated_at")
    if not isinstance(timestamp, str):
        raise ProductionIdentityError("production heartbeat has no timestamp")
    try:
        observed = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError("timezone missing")
        delta = (time.time() if now is None else now) - observed.timestamp()
    except ValueError as exc:
        raise ProductionIdentityError("invalid production heartbeat timestamp") from exc
    if delta < -30 or delta > 180:
        raise ProductionIdentityError("production heartbeat is stale or from the future")


def main(args: list[str]) -> int:
    if len(args) < 3 or args[1] not in ("remote", "verify"):
        print("usage: verify_v5_production_health.py remote CONFIG | verify CONFIG HEALTH", file=sys.stderr)
        return 2
    try:
        config = load_legacy_config(Path(args[2]))
        if args[1] == "remote" and len(args) == 3:
            print(config["remote"])
        elif args[1] == "verify" and len(args) == 4:
            path = Path(args[3])
            if path.is_symlink() or not path.is_file():
                raise ProductionIdentityError("missing production heartbeat file")
            if path.stat().st_size > 131072:
                raise ProductionIdentityError("production heartbeat exceeds size cap")
            record = json.loads(path.read_text(encoding="utf-8"))
            verify_v5_production_health(config, record)
            print("LIVE_PRODUCTION_V5_IDENTITY_VERIFIED=1")
        else:
            return 2
    except (ProductionIdentityError, OSError, ValueError) as exc:
        print("HOLD: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
