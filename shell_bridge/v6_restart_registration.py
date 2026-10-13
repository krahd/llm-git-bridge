#!/usr/bin/env python3
"""Conservative, local-only reversible v6 LaunchAgent registration operations.

These do not launch, stop, signal or invoke a daemon. They are intended to be
called ONLY after the guarded cutover's ownership, journal, approval and
mailbox gates, by an out-of-band macOS operator. A failed post-admission action
must preserve an explicit HOLD rather than restarting the old consumer.
"""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import re
import stat
import sys
import tempfile


class RegistrationHold(ValueError):
    pass


_LABEL = re.compile(r"(?:net\.laurenzo\.local-executor-bridge|io\.llm-git-bridge\.daemon|com\.tom\.chatgpt-shell-bridge)(?:[-.][a-zA-Z0-9._-]+)?\Z")


def _identity(plist: Path, label: str) -> bytes:
    if (not _LABEL.fullmatch(label) or not plist.is_absolute() or
            plist.is_symlink() or not plist.is_file()):
        raise RegistrationHold("invalid or linked LaunchAgent registration")
    try:
        st = plist.stat()
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or
                st.st_mode & 0o022 or st.st_size > 65536):
            raise RegistrationHold("LaunchAgent is not a private owned file")
        raw = plist.read_bytes()
        obj = plistlib.loads(raw)
        if not isinstance(obj, dict) or obj.get("Label") != label:
            raise RegistrationHold("LaunchAgent label does not match immutable registration")
        args = obj.get("ProgramArguments")
        if (not isinstance(args, list) or not all(isinstance(a, str) for a in args)
                or "daemon" not in args or not any("bridge.py" in a for a in args)):
            raise RegistrationHold("LaunchAgent does not point to a bridge daemon")
        return raw
    except RegistrationHold:
        raise
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        raise RegistrationHold("LaunchAgent registration is unreadable") from exc


def _agent_parent(path: Path) -> bool:
    parent = path.parent
    return (path.is_absolute() and parent.name == "LaunchAgents" and
            parent.parent.name == "Library" and not parent.is_symlink() and
            parent.is_dir())


def archive(source: Path, directory: Path, label: str) -> None:
    if not _agent_parent(source) or directory.parent != source.parent or (
            not directory.name.startswith("retired-v6-") or
            not re.fullmatch(r"retired-v6-[a-f0-9]{12}", directory.name)):
        raise RegistrationHold("archive must be a fresh subdirectory of LaunchAgents")
    _identity(source, label)
    if directory.exists() or directory.is_symlink():
        raise RegistrationHold("stale archive already exists; reconcile instead of retry")
    try:
        directory.mkdir(mode=0o700)
        target = directory / (label + ".plist")
        if target.exists() or target.is_symlink():
            raise RegistrationHold("archive target already exists")
        os.rename(source, target)
        _identity(target, label)
        if source.exists() or source.is_symlink():
            raise RegistrationHold("source LaunchAgent still registered")
    except RegistrationHold:
        raise
    except OSError as exc:
        raise RegistrationHold("source LaunchAgent archive is incomplete") from exc


def restore(archived: Path, destination: Path, label: str) -> None:
    if (not _agent_parent(destination) or
            archived.parent.parent != destination.parent or
            not archived.parent.name.startswith("retired-v6-")):
        raise RegistrationHold("restore paths are not a recognised private archive")
    if destination.exists() or destination.is_symlink():
        # A bootout may fail before archive, leaving original untouched. Never
        # overwrite this registration or guess whether it is safe to load.
        _identity(destination, label)
        if archived.exists() or archived.is_symlink():
            raise RegistrationHold("source exists both live and archived")
        return
    _identity(archived, label)
    try:
        os.rename(archived, destination)
        _identity(destination, label)
    except OSError as exc:
        raise RegistrationHold("source LaunchAgent restoration was not verified") from exc


def publish(staged: Path, destination: Path, label: str) -> None:
    if not _agent_parent(destination) or destination.name != label + ".plist":
        raise RegistrationHold("persistent target is not a direct LaunchAgent registration")
    raw = _identity(staged, label)
    if destination.exists() or destination.is_symlink():
        raise RegistrationHold("persistent candidate already registered; do not replace it")
    directory = destination.parent
    fd = None
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".v6-target-", dir=directory)
        with os.fdopen(fd, "wb") as stream:
            fd = None
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        # Link rather than os.replace so a concurrent registration is never
        # overwritten even if it appears after the initial absence check.
        os.link(tmp, destination, follow_symlinks=False)
        _identity(destination, label)
    except (OSError, RegistrationHold) as exc:
        raise RegistrationHold("persistent candidate registration was not verified") from exc
    finally:
        if fd is not None:
            os.close(fd)
        if tmp is not None and os.path.exists(tmp):
            os.unlink(tmp)


def quarantine(source: Path, archive: Path, label: str) -> None:
    if (not _agent_parent(source) or
            archive.parent.parent != source.parent or
            not archive.parent.name.startswith("retired-v6-") or
            archive.name != "failed-candidate-" + label + ".plist"):
        raise RegistrationHold("candidate quarantine path is invalid")
    if not source.exists() and not source.is_symlink():
        return
    _identity(source, label)
    if archive.exists() or archive.is_symlink():
        raise RegistrationHold("candidate quarantine destination is occupied")
    try:
        os.rename(source, archive)
        _identity(archive, label)
    except OSError as exc:
        raise RegistrationHold("candidate reboot registration could not be quarantined") from exc


def main(argv: list[str]) -> int:
    if len(argv) != 5 or argv[1] not in ("archive", "restore", "publish", "quarantine"):
        print("usage: v6_restart_registration.py archive|restore|publish|quarantine SOURCE DESTINATION LABEL",
              file=sys.stderr)
        return 2
    try:
        globals()[argv[1]](Path(argv[2]), Path(argv[3]), argv[4])
    except RegistrationHold as exc:
        print("V6_RESTART_REGISTRATION_HOLD: " + str(exc), file=sys.stderr)
        return 1
    print("V6_RESTART_REGISTRATION_" + argv[1].upper() + "_VERIFIED=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
