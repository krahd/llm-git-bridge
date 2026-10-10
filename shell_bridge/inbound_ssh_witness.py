#!/usr/bin/env python3
"""One-time witness from an already authenticated, separate inbound SSH session.

This cannot enable SSH or establish internet reachability. Operator must run
the create command in a genuine SSH session from another device; SSH_CONNECTION
then supplies the peer tuple. No agent-facing network service is created.
"""
from __future__ import annotations
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import stat
import sys
import time

_TOKEN = re.compile(r"[0-9a-f]{48}\Z")


def parse_connection(raw: str) -> dict:
    fields = raw.split()
    if len(fields) != 4:
        raise ValueError("SSH_CONNECTION is missing or malformed")
    client, client_port, server, server_port = fields
    try:
        source_ip = ipaddress.ip_address(client)
        target_ip = ipaddress.ip_address(server)
        source_port = int(client_port)
        target_port = int(server_port)
    except ValueError as exc:
        raise ValueError("invalid SSH session network peer") from exc
    if (source_ip.is_loopback or source_ip.is_unspecified
            or target_ip.is_unspecified or not 1 <= source_port <= 65535
            or not 1 <= target_port <= 65535):
        raise ValueError("SSH witness must come from a non-loopback peer")
    return {
        "client_ip": str(source_ip), "client_port": source_port,
        "server_ip": str(target_ip), "server_port": target_port,
    }


def _check_destination(path: Path, token: str) -> None:
    if not _TOKEN.fullmatch(token) or not path.is_absolute() or path.is_symlink():
        raise ValueError("invalid inbound SSH witness target")
    parent = path.parent
    st = parent.stat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ValueError("inbound SSH witness directory is not private")


def create(path: Path, token: str, connection: str, *, timestamp: float | None = None) -> dict:
    _check_destination(path, token)
    evidence = {
        "schema": 1, "kind": "inbound_ssh_session",
        "nonce": token, "account": pwd.getpwuid(os.getuid()).pw_name,
        "created_at": time.time() if timestamp is None else timestamp,
        **parse_connection(connection),
    }
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(evidence, stream, sort_keys=True)
        stream.write(chr(10))
        stream.flush()
        os.fsync(stream.fileno())
    return evidence


def verify(path: Path, token: str, *, now: float | None = None) -> dict:
    _check_destination(path, token)
    if path.is_symlink():
        raise ValueError("inbound SSH witness symlink rejected")
    evidence = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(evidence, dict) or (
        evidence.get("kind") != "inbound_ssh_session"
        or evidence.get("schema") != 1
        or evidence.get("nonce") != token
        or evidence.get("account") != pwd.getpwuid(os.getuid()).pw_name
    ):
        raise ValueError("inbound SSH witness identity mismatch")
    stamp = evidence.get("created_at")
    current = time.time() if now is None else now
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not 0 <= current - stamp <= 180:
        raise ValueError("inbound SSH witness is stale or from the future")
    peer = " ".join(str(evidence.get(name, "")) for name in (
        "client_ip", "client_port", "server_ip", "server_port"))
    if parse_connection(peer) != {k: evidence.get(k) for k in (
        "client_ip", "client_port", "server_ip", "server_port")}:
        raise ValueError("inbound SSH witness peer is invalid")
    return {
        "inbound_ssh_nonlocal_session_witness": True,
        "external_internet_reachability_verified": False,
        "network_peer": evidence["client_ip"],
        "account": evidence["account"],
        "mutations_performed": False,
    }


def main(args: list[str]) -> int:
    if len(args) != 4 or args[1] not in ("create", "verify"):
        print("usage: inbound_ssh_witness.py create|verify ABSOLUTE_RECEIPT_PATH NONCE", file=sys.stderr)
        return 2
    action, path, nonce = args[1:]
    try:
        if action == "create":
            create(Path(path), nonce, os.environ.get("SSH_CONNECTION", ""))
            print("INBOUND_SSH_SESSION_WITNESS_WRITTEN=1")
        else:
            print(json.dumps(verify(Path(path), nonce), sort_keys=True))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print("INBOUND_SSH_WITNESS_HOLD: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
