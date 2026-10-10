#!/usr/bin/env python3
"""One-time local SSH host enrollment without network trust-on-first-use.

Uses only already-verified OpenSSH known_hosts entries. Never invokes an SSH
connection or obtains new host keys from the network. Writes a separate,
operator-owned host policy after explicit fingerprint confirmation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile


class EnrollmentError(ValueError):
    pass


_HOST = re.compile(r"[a-z0-9][a-z0-9.-]{0,252}\Z")
_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")


def _safe_existing(path: Path, *, private: bool = False) -> None:
    if not path.is_absolute() or path.is_symlink():
        raise EnrollmentError("unsafe symlink or nonabsolute SSH material")
    try:
        meta = path.stat()
    except OSError as exc:
        raise EnrollmentError("required SSH material does not exist") from exc
    if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid()
            or meta.st_mode & (0o077 if private else 0o022)):
        raise EnrollmentError("unsafe SSH file owner or permissions")


def trusted_host_lines(source: Path, host: str, port: int) -> tuple[str, str]:
    _safe_existing(source)
    label = host if port == 22 else f"[{host}]:{port}"
    result = subprocess.run(
        ["/usr/bin/ssh-keygen", "-F", label, "-f", str(source)],
        capture_output=True, text=True, check=False, timeout=10,
    )
    if result.returncode != 0:
        raise EnrollmentError("host not present in existing trusted known_hosts; verify independently")
    lines = [line for line in result.stdout.splitlines()
             if line and not line.startswith("#") and len(line.split()) >= 3]
    if not lines:
        raise EnrollmentError("no usable preverified SSH host keys found")
    with tempfile.TemporaryDirectory() as td:
        sample = Path(td) / "host_keys"
        sample.write_text("\n".join(lines) + "\n", encoding="utf-8")
        fingerprints = subprocess.run(
            ["/usr/bin/ssh-keygen", "-lf", str(sample)],
            capture_output=True, text=True, check=False, timeout=10,
        )
        if fingerprints.returncode != 0 or not fingerprints.stdout.strip():
            raise EnrollmentError("could not independently fingerprint known host")
    return "\n".join(lines) + "\n", fingerprints.stdout.strip()


def enroll(directory: Path, known_source: Path, identity: Path, host: str,
           account: str, port: int, confirmed: bool) -> Path:
    if not confirmed:
        raise EnrollmentError("explicit SSH host confirmation is required")
    if not _HOST.fullmatch(host) or not _USER.fullmatch(account):
        raise EnrollmentError("unsafe SSH host or username")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise EnrollmentError("invalid SSH port")
    _safe_existing(identity, private=True)
    keys, _ = trusted_host_lines(known_source, host, port)
    if directory.exists() or directory.is_symlink():
        raise EnrollmentError("SSH enrollment already exists; do not overwrite")
    if not directory.parent.is_dir() or directory.parent.is_symlink():
        raise EnrollmentError("SSH policy parent path unsafe")
    directory.mkdir(mode=0o700)
    policy = {
        "schema": 1, "host": host, "user": account, "port": port,
        "identity_file": str(identity),
    }
    for name, contents in (
        ("known_hosts", keys),
        ("ssh-policy.json", json.dumps(policy, sort_keys=True, indent=2) + "\n"),
    ):
        path = directory / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
    return directory


def _ask(prompt: str, default: str = "") -> str:
    sys.stderr.write(prompt + (f" [{default}]" if default else "") + ": ")
    sys.stderr.flush()
    line = sys.stdin.readline()
    if not line:
        raise EnrollmentError("operator input unavailable")
    return line.strip() or default


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: setup_v6_pinned_ssh.py SOURCE_SHA ALLOWED_ROOT", file=sys.stderr)
        return 2
    source, allowed_raw = argv[1:]
    if len(source) != 40 or any(c not in "0123456789abcdef" for c in source):
        return 2
    allowed = Path(allowed_raw).expanduser().resolve()
    policy_dir = Path.home() / ".config" / ("bridge-v6-ssh-" + source[:12])
    try:
        host = _ask("Pinned SSH hostname (already present in known_hosts)").lower()
        account = _ask("Pinned SSH user")
        port = int(_ask("SSH port", "22"))
        identity = Path(_ask("Private SSH identity path", str(Path.home() / ".ssh/id_ed25519"))).expanduser()
        known = Path(_ask("Previously verified known_hosts path", str(Path.home() / ".ssh/known_hosts"))).expanduser()
        for path in (identity, known, policy_dir):
            try:
                path.resolve().relative_to(allowed)
            except ValueError:
                pass
            else:
                raise EnrollmentError("SSH credentials or policy must not be agent-writable")
        lines, fingerprints = trusted_host_lines(known, host, port)
        sys.stderr.write(
            f"Verify SSH target {account}@{host}:{port} using {identity}\n"
            f"Previously trusted host key fingerprint(s):\n{fingerprints}\n"
        )
        confirmed = _ask("Type PIN SSH to accept this exact host and key(s)") == "PIN SSH"
        enrolled = enroll(policy_dir, known, identity, host, account, port, confirmed)
    except (EnrollmentError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print("SSH_ENROLLMENT_HELD: " + str(exc), file=sys.stderr)
        return 1
    print(enrolled)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
