#!/usr/bin/env python3
"""Explicit, operator-local enrollment for the Ursula v6 trusted test helper.

NEVER execute via the bridge mailbox or its workspace coordinator. The owner
must run --apply in an interactive Mac Terminal after reviewing the diff.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
from datetime import datetime, timezone

NAME = "ursula-ollama-eval"
ACTIONS = ["ollama-status", "core-c3", "core-abc2", "social-c3"]


def die(msg: str) -> None:
    raise SystemExit("Refusing enrollment: " + msg)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_exclusive(path: Path, payload: bytes, mode: int) -> None:
    """Create sensitive files with restrictive mode from the first byte."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, mode)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true", help="Operator approval to modify trusted local config and restart v6")
    args = p.parse_args()
    if sys.platform != "darwin":
        die("enrollment must run locally on macOS")
    home = Path.home().resolve()
    install = home / ".local/share/local-executor-bridge"
    config_path = home / ".config/local-executor-bridge/config.json"
    src = Path(__file__).resolve().parent / "ursula_ollama_eval_helper.py"
    dst = install / src.name
    if not src.is_file() or src.is_symlink():
        die("reviewed helper source is unavailable or is symlinked")
    if not (install / "bridge.py").is_file() or not config_path.is_file():
        die("installed v6 bridge or configuration missing")
    # Identify the exact forbidden link without following or changing it.
    # The operator must inspect its ownership and intended target separately.
    for label, candidate in (
        ("configuration file", config_path),
        ("bridge installation directory", install),
        ("installed Ursula helper", dst),
    ):
        if candidate.is_symlink():
            die(f"{label} is a symlink: {candidate} -> {os.readlink(candidate)!r}; no changes made")
    cfg = json.loads(config_path.read_text("utf-8"))
    allowed_root = Path(cfg["allowed_root"]).resolve()
    repo = allowed_root / "projects/ursula/apps/storytelling/prototype-0"
    if cfg.get("operator_confirmation_mode") not in {"dialog", "auto"}:
        die("native operator approval is not enabled")
    if not repo.is_dir() or repo.resolve() != repo:
        die("expected Ursula repo is absent or symlinked")
    if dst.exists() and sha256(dst) != sha256(src):
        die("installed helper differs; manual reconciliation required")
    descriptor = {
        "executable": str(dst),
        "executable_sha256": sha256(src),
        "permitted_roots": [str(repo)],
        "permitted_actions": ACTIONS,
    }
    registry = cfg.get("trusted_operations", {})
    if not isinstance(registry, dict):
        die("trusted operation registry is malformed")
    if NAME in registry and registry[NAME] != descriptor:
        die("trusted operation already exists with different policy; manual review required")
    health_path = Path(cfg["state_dir"]) / "health.json"
    if not health_path.is_file():
        die("current v6 health file absent")
    health = json.loads(health_path.read_text("utf-8"))
    if health.get("bridge_version") != "6" or health.get("build_code_integrity") != "matches_manifest":
        die("v6 runtime health/build integrity unverified")
    if health.get("active_requests") or health.get("state", {}).get("started_without_finished", 0):
        die("bridge has active or unfinished requests; defer enrollment")
    label = "net.laurenzo.local-executor-bridge"
    agent = home / "Library/LaunchAgents" / (label + ".plist")
    if not agent.is_file():
        die("known launch agent missing; do not guess another service name")
    with agent.open("rb") as f:
        if plistlib.load(f).get("Label") != label:
            die("launch agent label mismatch")
    print("Bridge build:", health.get("build_source_commit"))
    print("Operator-owned helper:", dst)
    print("Helper SHA-256:", descriptor["executable_sha256"])
    print("Ursula root:", repo)
    print("Approved actions:", ", ".join(ACTIONS))
    print("Per-action native approval remains REQUIRED.")
    if not args.apply:
        print("DRY RUN: nothing modified. Rerun with --apply after review.")
        return
    # Config is sensitive: preserve existing permissions and create a unique
    # rollback copy. Never touch an existing different trusted registration.
    if NAME in registry and dst.is_file():
        print("Already enrolled with matching SHA; no restart needed.")
        return
    if not dst.exists():
        tmp = install / (src.name + ".tmp-" + str(os.getpid()))
        write_exclusive(tmp, src.read_bytes(), 0o700)
        os.replace(tmp, dst)
    registry = dict(registry)
    registry[NAME] = descriptor
    cfg["trusted_operations"] = registry
    backup = config_path.with_name("config.json.before-ursula-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    write_exclusive(backup, config_path.read_bytes(), 0o600)
    temporary = config_path.with_name(config_path.name + ".tmp-" + str(os.getpid()))
    write_exclusive(temporary, (json.dumps(cfg, indent=2, sort_keys=True) + "\n").encode("utf-8"), 0o600)
    os.replace(temporary, config_path)
    # Do not restart through the bridge itself: the owner is already in an
    # interactive Terminal and must initiate this --apply explicitly.
    cmd = ["/bin/launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"]
    print("Operator-requested restart:", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        die(f"config enrolled but bridge restart failed ({exc}); backup at {backup}")
    print("Enrolled. Submit a trusted ollama-status request to verify the approval and execution path.")
    print("Configuration rollback copy:", backup)


if __name__ == "__main__":
    main()
