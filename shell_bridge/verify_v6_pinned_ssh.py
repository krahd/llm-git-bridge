#!/usr/bin/env python3
"""Hash-bound acceptance request for installed, no-prompt pinned SSH status."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
import sys


def make_request(rid: str, cwd: str) -> dict:
    return {
        "protocol": 1, "id": rid, "cwd": cwd,
        "trusted_operation": {"name": "ssh-pinned-readonly", "action": "status"},
        "explanation": "Verify operator-installed, read-only host-pinned SSH status capability",
        "write_scope": "auto", "timeout_seconds": 60,
        "operation_id": rid,
    }


def verify(request_bytes: bytes, result: dict, rid: str) -> None:
    expected = {
        "protocol": 1, "id": rid, "kind": "result",
        "status": "completed", "exit_code": 0,
        "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
    }
    for key, value in expected.items():
        if result.get(key) != value:
            raise ValueError("SSH smoke identity or completion mismatch: " + key)
    if result.get("write_scope", {}).get("effective") != "trusted_operation":
        raise ValueError("SSH smoke ran outside trusted operation")
    approval = result.get("operator_confirmation")
    if not isinstance(approval, dict) or (
        approval.get("required") is not False
        or approval.get("mode") != "operator_installed_capability"
        or approval.get("approved") is not True
        or approval.get("category") != "pinned_ssh_readonly"
    ):
        raise ValueError("SSH smoke did not use exact installed no-prompt authorization")
    out = result.get("stdout_text")
    if not isinstance(out, str) or not 0 < len(out) <= 65536:
        raise ValueError("SSH smoke produced no bounded stdout")


def main(argv: list[str]) -> int:
    if len(argv) != 5 or argv[1] not in ("create", "verify"):
        print("usage: verify_v6_pinned_ssh.py create|verify REQUEST ID CWD|RESULT", file=sys.stderr)
        return 2
    action, source, rid, arg = argv[1:]
    try:
        if (not rid.startswith("v6-ssh-") or
                re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", rid) is None):
            raise ValueError("invalid SSH smoke request identity")
        if action == "create":
            if not Path(arg).is_dir():
                raise ValueError("SSH smoke cwd does not exist")
            Path(source).write_text(json.dumps(
                make_request(rid, arg), sort_keys=True, separators=(",", ":")),
                encoding="utf-8")
        else:
            verify(Path(source).read_bytes(),
                   json.loads(Path(arg).read_text(encoding="utf-8")), rid)
            print("PINNED_SSH_READONLY_ACCEPTED=1")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print("PINNED_SSH_READONLY_NOT_VERIFIED: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
