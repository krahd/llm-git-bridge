#!/usr/bin/env python3
"""Read-only fail-closed detection of login-restart bridge mailbox claimants.

Only direct LaunchAgents/*.plist registrations are inspected; do not assume
launchctl's currently loaded services enumerate future login auto-start jobs.
This gate cannot establish OS-level exclusive flock or arbitrary login hooks.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import sys
import stat


class RestartRegistrationHold(ValueError):
    pass


def bridge_label(label: str) -> bool:
    s = label.lower()
    return (
        s.startswith("io.llm-git-bridge.")
        or s.startswith("com.tom.chatgpt-shell-bridge")
        or s.startswith("net.laurenzo.local-executor-bridge")
        or s.startswith("net.laurenzo.mac-executor-bridge")
        or (s.startswith("net.laurenzo.") and "bridge" in s)
    )


def _read_private_bridge_config(path: Path, home: Path) -> dict:
    # An untrusted or mutable config makes the future-login mailbox identity
    # contingent on agent-writable state. Never certify an arbitrary path
    # merely because its JSON currently contains another mailbox ID.
    root = home / ".config"
    if (not path.is_absolute() or path.name != "config.json" or
            path.parent.parent != root or
            not path.is_relative_to(root)):
        raise ValueError("bridge config is outside managed private config")
    for item in (root, path.parent, path):
        info = item.lstat()
        if (info.st_uid != os.getuid() or stat.S_ISLNK(info.st_mode) or
                info.st_mode & 0o022):
            raise ValueError("bridge config path is linked or writable by others")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        meta = os.fstat(handle.fileno())
        if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid()
                or meta.st_mode & 0o077 or meta.st_size > 2 * 1024 * 1024):
            raise ValueError("bridge config is not a private bounded file")
        raw = handle.read(meta.st_size + 1)
        if len(raw) != meta.st_size:
            raise ValueError("bridge config changed during inspection")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("bridge config is malformed")
    return data


def inspect_agents(directory: Path, source_label: str, root_id: str,
                   *, limit: int = 4096) -> dict:
    if (not directory.is_absolute() or directory.is_symlink() or
            not directory.is_dir() or
            not source_label or not root_id or
            not bridge_label(source_label)):
        raise RestartRegistrationHold("invalid LaunchAgents source or mailbox")
    owners: list[str] = []
    checked = 0
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if not entry.name.endswith(".plist"):
                    continue
                checked += 1
                if checked > limit:
                    raise RestartRegistrationHold("too many LaunchAgent plists")
                label = entry.name[:-6]
                suspect = bridge_label(label)
                if entry.is_symlink():
                    # Any bridge-prefixed linked plist could redirect to a
                    # different executable on next login.
                    if suspect:
                        raise RestartRegistrationHold("linked bridge LaunchAgent registration")
                    continue
                if not entry.is_file(follow_symlinks=False):
                    if suspect:
                        raise RestartRegistrationHold("irregular bridge LaunchAgent registration")
                    continue
                is_bridge_command = False
                try:
                    if entry.stat(follow_symlinks=False).st_size > 65536:
                        raise ValueError("plist too large")
                    fd = os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW)
                    with os.fdopen(fd, "rb") as stream:
                        value = plistlib.load(stream)
                    if not isinstance(value, dict):
                        raise ValueError("invalid plist object")
                    args = value.get("ProgramArguments")
                    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                        if suspect:
                            raise ValueError("bridge process args unavailable")
                        continue
                    is_bridge_command = any(
                        "bridge.py" in a or "shell_bridge" in a
                        for a in args)
                    if not suspect and not is_bridge_command:
                        continue
                    if value.get("Label") != label:
                        raise ValueError("launchd label and filename differ")
                    indexes = [i for i, x in enumerate(args) if x == "--config"]
                    alternatives = [a[9:] for a in args if a.startswith("--config=")]
                    if len(indexes) + len(alternatives) != 1:
                        raise ValueError("bridge configuration is ambiguous")
                    if indexes:
                        index = indexes[0]
                        if index + 1 >= len(args):
                            raise ValueError("bridge configuration is absent")
                        cfg = Path(args[index + 1])
                    else:
                        cfg = Path(alternatives[0])
                    data = _read_private_bridge_config(cfg, directory.parent.parent)
                    if data.get("drive_root_folder_id") == root_id:
                        owners.append(label)
                except (OSError, ValueError, TypeError,
                        plistlib.InvalidFileException) as exc:
                    if suspect or is_bridge_command:
                        raise RestartRegistrationHold(
                            "unverifiable bridge LaunchAgent registration") from exc
    except RestartRegistrationHold:
        raise
    except OSError as exc:
        raise RestartRegistrationHold("LaunchAgents folder cannot be enumerated") from exc
    if owners != [source_label]:
        raise RestartRegistrationHold(
            "unexpected or missing future-login claimant for production mailbox")
    return {
        "kind": "v6_login_claimant_inventory",
        "schema": 1, "checked_plists": checked,
        "registered_production_labels": owners,
        "eligible_for_source_archive": True,
        "mutations_performed": False,
        "login_recovery_verified": False,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: verify_v6_login_claimants.py LAUNCHAGENTS_DIR SOURCE_LABEL ROOT_ID",
              file=sys.stderr)
        return 2
    try:
        report = inspect_agents(Path(argv[1]), argv[2], argv[3])
    except RestartRegistrationHold as exc:
        print("LOGIN_CLAIMANT_HOLD: " + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
