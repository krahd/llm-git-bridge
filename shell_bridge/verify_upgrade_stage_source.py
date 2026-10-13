#!/usr/bin/env python3
"""Read-only identity gate for preparing a v6 replacement from production.

This does not approve loading the candidate or touching the live journal.
It verifies a known source config and exact mailbox/journal identities
without reporting credentials, paths to keys, or arbitrary config fields.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys

FIELDS = ("remote", "base_path", "drive_root_folder_id",
          "requests_folder_id", "results_folder_id", "bridge_instance_id",
          "state_dir", "allowed_root", "shell")


class StageSourceError(ValueError):
    pass


def _safe_owned(path: Path, home: Path, *, directory: bool) -> bool:
    if not path.is_absolute() or home not in path.parents:
        return False
    current = path
    while current != home:
        try:
            info = current.lstat()
        except OSError:
            return False
        if stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid():
            return False
        if current != path:
            if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
                return False
        else:
            if directory and (not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077):
                return False
            if not directory and (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077):
                return False
        current = current.parent
    return True


def _remote(value: str) -> str:
    return value if value.endswith(":") else value + ":"


def verify(source_path: Path, expected: dict, *, home: Path | None = None) -> None:
    home = (home or Path.home()).absolute()
    if (source_path.name != "config.json" or
            source_path.parent.parent != home / ".config" or
            not _safe_owned(source_path, home, directory=False)):
        raise StageSourceError("source config is outside trusted private config directory")
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise StageSourceError("source configuration unreadable") from exc
    if not isinstance(source, dict):
        raise StageSourceError("source configuration is not a JSON object")
    if any(not isinstance(source.get(key), str) or not source[key]
           for key in FIELDS):
        raise StageSourceError("source configuration identity is incomplete")
    if any(not isinstance(expected.get(key), str) or not expected[key]
           for key in FIELDS):
        raise StageSourceError("replacement identity is incomplete")
    for key in FIELDS:
        left, right = source[key], expected[key]
        if key == "remote":
            left, right = _remote(left), _remote(right)
        # Exact durable state and allowed-root strings are required. A
        # symlink/relative alias could change targets between stage and
        # cutover, even if it resolves to the same path at inspection time.
        if left != right:
            raise StageSourceError("replacement differs from source " + key)
    state = Path(source["state_dir"])
    if not _safe_owned(state, home, directory=True):
        raise StageSourceError("existing production journal must be a private owned directory")
    if (home / ".local" / "state") not in state.parents:
        raise StageSourceError("production journal outside managed local state")


def main(argv: list[str]) -> int:
    if len(argv) != 11:
        print("SOURCE_STAGE_HOLD: expected source path plus nine identity fields", file=sys.stderr)
        return 2
    expected = dict(zip(FIELDS, argv[2:]))
    try:
        verify(Path(argv[1]), expected)
    except StageSourceError as exc:
        print("SOURCE_STAGE_HOLD: " + str(exc), file=sys.stderr)
        return 1
    print("SOURCE_STAGE_IDENTITY_VERIFIED=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
