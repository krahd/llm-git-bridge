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


def main() -> int:
    if sys.platform != "darwin":
        print("INBOUND_SSH_HOLD: macOS only", file=sys.stderr)
        return 2
    try:
        user = pwd.getpwuid(os.getuid()).pw_name
        commands = (
            ["/usr/sbin/systemsetup", "-getremotelogin"],
            ["/usr/sbin/dseditgroup", "-o", "checkmember",
             "-m", user, "com.apple.access_ssh"],
        )
        output = []
        for argv in commands:
            run = subprocess.run(argv, capture_output=True, text=True,
                                 timeout=10, check=False)
            if run.returncode != 0:
                raise ValueError("Remote Login settings could not be read")
            output.append(run.stdout)
        print(json.dumps(verify(*output, username=user), sort_keys=True))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print("INBOUND_SSH_HOLD: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
