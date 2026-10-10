#!/usr/bin/env python3
"""Read-only macOS inbound SSH safety preflight; never enables Remote Login.

Checks local Remote Login switch and that the current user is explicitly in
the SSH-allowed group. It cannot prove an external route or live handshake.
"""
from __future__ import annotations

import json
import os
import pwd
import re
import subprocess
import sys

_LOGIN_ON = re.compile(r"^Remote Login:\s*On\s*$", re.IGNORECASE)
_MEMBER_YES = re.compile(r"^yes\s+\S+\s+is a member of com\.apple\.access_ssh\s*$",
                         re.IGNORECASE)


def verify(remote_login_stdout: str, membership_stdout: str, username: str) -> dict:
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", username):
        raise ValueError("invalid local login identity")
    lines = remote_login_stdout.strip().splitlines()
    if len(lines) != 1 or not _LOGIN_ON.fullmatch(lines[0].strip()):
        raise ValueError("Remote Login is off or could not be verified")
    membership_lines = membership_stdout.strip().splitlines()
    expected = f"yes {username} is a member of com.apple.access_ssh"
    if len(membership_lines) != 1 or membership_lines[0].strip().lower() != expected.lower():
        raise ValueError("SSH allowed-user restriction not verified for the current account")
    return {
        "kind": "inbound_ssh_local_readiness",
        "remote_login_on": True,
        "account_explicitly_allowed": True,
        "external_connectivity_verified": False,
        "host_authentication_verified": False,
        "mutations_performed": False,
    }


def launchctl_remote_login_status(output: str) -> str:
    # launchctl print-disabled reports the SSH service with OS-specific values.
    entries = []
    for line in output.splitlines():
        if "com.openssh.sshd" not in line:
            continue
        parts = line.strip().replace('"', '').split("=>")
        if len(parts) != 2 or parts[0].strip() != "com.openssh.sshd":
            raise ValueError("invalid SSH launchd state")
        entries.append(parts[1].strip().lower())
    if len(entries) != 1:
        raise ValueError("launchd SSH service state is ambiguous")
    if entries[0] in ("enabled", "false"):
        return "Remote Login: On"
    if entries[0] in ("disabled", "true"):
        return "Remote Login: Off"
    raise ValueError("unknown SSH launchd state")


def main() -> int:
    if sys.platform != "darwin":
        print("INBOUND_SSH_HOLD: macOS only", file=sys.stderr)
        return 2
    try:
        user = pwd.getpwuid(os.getuid()).pw_name
        # systemsetup -getremotelogin may require administrator privileges.
        # First try that read-only query; fall back to the documented launchd
        # disabled map rather than asking for elevated system changes.
        query = subprocess.run(
            ["/usr/sbin/systemsetup", "-getremotelogin"],
            capture_output=True, text=True, timeout=10, check=False)
        if query.returncode == 0 and query.stdout.strip() == "Remote Login: On":
            status = query.stdout
        elif query.returncode == 0 and query.stdout.strip() == "Remote Login: Off":
            raise ValueError("Remote Login is off")
        else:
            alternate = subprocess.run(
                ["/bin/launchctl", "print-disabled", "system"],
                capture_output=True, text=True, timeout=10, check=False)
            if alternate.returncode != 0:
                raise ValueError("Remote Login settings could not be read")
            status = launchctl_remote_login_status(alternate.stdout)
        member = subprocess.run(
            ["/usr/sbin/dseditgroup", "-o", "checkmember",
             "-m", user, "com.apple.access_ssh"],
            capture_output=True, text=True, timeout=10, check=False)
        if member.returncode != 0:
            raise ValueError("SSH allowed-user membership could not be read")
        print(json.dumps(verify(status, member.stdout, username=user), sort_keys=True))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print("INBOUND_SSH_HOLD: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
