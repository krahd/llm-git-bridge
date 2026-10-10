#!/usr/bin/env python3
"""Pinned remote SSH action runner; no arbitrary provider-supplied command.

An operator must provision the policy directory and explicitly register this
exact helper SHA in the trusted operation registry. Neither host, identity,
command, nor SSH option is accepted from the agent's request.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import selectors
import signal
import stat
import subprocess
import sys
import time

_ACTION = re.compile(r"[a-z][a-z0-9-]{2,63}\Z")
_HOST = re.compile(r"[a-z0-9][a-z0-9.-]{0,252}\Z")
_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
_COMMANDS = {"status": "/usr/bin/uptime", "identity": "/usr/bin/id -un"}
_MAX_BYTES = 65536


class SSHPolicyError(ValueError):
    pass


def _regular_owner_file(path: Path, parent: Path) -> None:
    if not path.is_absolute() or path.parent != parent or path.is_symlink():
        raise SSHPolicyError("unsafe SSH policy file path")
    try:
        info = path.stat()
    except OSError as exc:
        raise SSHPolicyError("SSH policy file unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise SSHPolicyError("SSH policy file ownership or mode invalid")


def load_policy(directory: Path, action: str) -> tuple[list[str], int]:
    if not directory.is_absolute() or directory.is_symlink():
        raise SSHPolicyError("policy directory must be absolute and not a symlink")
    try:
        info = directory.stat()
    except OSError as exc:
        raise SSHPolicyError("SSH policy directory unavailable") from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise SSHPolicyError("SSH policy directory ownership or mode invalid")
    if not _ACTION.fullmatch(action):
        raise SSHPolicyError("invalid SSH action token")
    path = directory / "ssh-policy.json"
    known_hosts = directory / "known_hosts"
    _regular_owner_file(path, directory)
    _regular_owner_file(known_hosts, directory)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SSHPolicyError("invalid SSH policy") from exc
    required = {"schema", "host", "user", "port", "identity_file"}
    if not isinstance(data, dict) or type(data.get("schema")) is not int or (
        data.get("schema") == 1 and set(data) != required
    ) or (
        data.get("schema") == 2 and set(data) != required | {"commands"}
    ) or data.get("schema") not in (1, 2):
        raise SSHPolicyError("SSH policy fields are not pinned")
    host, user, port, identity = (data[key] for key in ("host", "user", "port", "identity_file"))
    if not isinstance(host, str) or not _HOST.fullmatch(host):
        raise SSHPolicyError("invalid pinned hostname")
    if not isinstance(user, str) or not _USER.fullmatch(user):
        raise SSHPolicyError("invalid pinned SSH account")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise SSHPolicyError("invalid pinned SSH port")
    if not isinstance(identity, str):
        raise SSHPolicyError("invalid SSH identity")
    key = Path(identity)
    if not key.is_absolute() or key.is_symlink() or not key.is_file():
        raise SSHPolicyError("SSH identity file unavailable or symlinked")
    key_info = key.stat()
    if key_info.st_uid != os.getuid() or key_info.st_mode & 0o077:
        raise SSHPolicyError("SSH private identity ownership or permissions invalid")
    commands = data.get("commands", {})
    if not isinstance(commands, dict) or len(commands) > 32:
        raise SSHPolicyError("invalid registered SSH commands")
    for name, tokens in commands.items():
        if not isinstance(name, str) or not _ACTION.fullmatch(name) or name in _COMMANDS:
            raise SSHPolicyError("invalid or reserved SSH action")
        if not isinstance(tokens, list) or not 1 <= len(tokens) <= 24:
            raise SSHPolicyError("invalid pinned remote argv")
        if any(not isinstance(token, str) or not token or len(token) > 512
               or any(ord(ch) < 32 or ord(ch) == 127 for ch in token)
               for token in tokens):
            raise SSHPolicyError("unsafe pinned remote argv")
        if not tokens[0].startswith("/") or "//" in tokens[0] or ".." in tokens[0].split("/"):
            raise SSHPolicyError("remote executable must have a normalized absolute path")
    if action in _COMMANDS:
        remote_command = _COMMANDS[action]
    elif action in commands:
        # This is a fixed operator-installed command, not provider shell text.
        # Quoting prevents an argument from changing remote shell structure.
        remote_command = shlex.join(commands[action])
    else:
        raise SSHPolicyError("unregistered SSH action")
    # This helper invokes one operator-pinned remote command.
    # Disable user SSH configuration, arbitrary options, proxy helpers, shell
    # interactivity and forwarding even when the Mac user's dotfiles differ.
    argv = [
        "/usr/bin/ssh", "-F", "/dev/null", "-T", "-n",
        "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "IdentitiesOnly=yes", "-o", "ClearAllForwardings=yes",
        "-o", "ForwardAgent=no", "-o", "PermitLocalCommand=no",
        "-o", "ControlMaster=no", "-o", "ProxyCommand=none",
        "-o", "ConnectTimeout=8",
        "-o", "UserKnownHostsFile=" + str(known_hosts),
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "UpdateHostKeys=no",
        "-o", "VerifyHostKeyDNS=no",
        "-i", str(key), "-p", str(port),
        user + "@" + host, remote_command,
    ]
    return argv, 25


class SSHOutputLimit(Exception):
    """Remote process exceeded a hard stream cap before it exited."""


def run_bounded(argv: list[str], timeout: int) -> tuple[int, bytes, bytes]:
    """Drain pipes incrementally; never accumulate arbitrary remote output.

    All failure exits stop the *local* SSH process group. A remote timeout may
    still be indeterminate, so the caller must never automatically resubmit.
    """
    proc = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, start_new_session=True, close_fds=True,
    )
    streams = selectors.DefaultSelector()
    stdout = bytearray()
    stderr = bytearray()
    completed = False
    deadline = time.monotonic() + timeout
    try:
        for pipe, sink in ((proc.stdout, stdout), (proc.stderr, stderr)):
            streams.register(pipe, selectors.EVENT_READ, sink)
        while streams.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            for key, _ in streams.select(remaining):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    streams.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                sink = key.data
                if len(sink) + len(chunk) > _MAX_BYTES:
                    raise SSHOutputLimit("remote output exceeded fixed cap")
                sink.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(argv, timeout)
        code = proc.wait(timeout=remaining)
        completed = True
        return code, bytes(stdout), bytes(stderr)
    finally:
        streams.close()
        if not completed:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None and not pipe.closed:
                pipe.close()


def execute(action: str, directory: str) -> int:
    argv, timeout = load_policy(Path(directory), action)
    try:
        code, stdout, stderr = run_bounded(argv, timeout)
    except SSHOutputLimit:
        print("PINNED_SSH_OUTPUT_LIMIT", file=sys.stderr)
        return 76
    except (OSError, subprocess.TimeoutExpired):
        print("PINNED_SSH_INDETERMINATE_OR_UNAVAILABLE", file=sys.stderr)
        return 76
    if code != 0:
        print("PINNED_SSH_FAILED; CHECK HOST KEY, NETWORK AND REMOTE ACCOUNT", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(stdout)
    return 0


def main(args: list[str]) -> int:
    if len(args) != 3:
        print("usage: ssh_pinned_helper.py ACTION PINNED_POLICY_DIRECTORY", file=sys.stderr)
        return 2
    try:
        return execute(args[1], args[2])
    except SSHPolicyError as exc:
        print("PINNED_SSH_DENIED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
