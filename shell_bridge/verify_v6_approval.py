#!/usr/bin/env python3
"""Generate and verify fixed, one-time operator-approval acceptance canaries."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

MARKER = "bridge_mailbox_ownership_inventory"


def request(rid: str, cwd: str) -> dict:
    return {
        "protocol": 1,
        "id": rid,
        "cwd": cwd,
        "trusted_operation": {"name": "bridge-mailbox-inspect", "action": "inspect"},
        "explanation": "Migration acceptance: verify a pinned, read-only trusted helper and native approval UI",
        "write_scope": "auto",
        "timeout_seconds": 60,
    }


def verify(raw: bytes, result: dict, rid: str, choice: str) -> None:
    if choice not in ("allow", "deny"):
        raise ValueError("unknown expected choice")
    required = {"protocol": 1, "id": rid,
                "request_sha256": hashlib.sha256(raw).hexdigest()}
    for field, expected in required.items():
        if result.get(field) != expected:
            raise ValueError("approval identity mismatch: " + field)
    confirmation = result.get("operator_confirmation")
    if not isinstance(confirmation, dict):
        raise ValueError("native operator confirmation missing")
    if (confirmation.get("required") is not True or
        confirmation.get("category") != "trusted_operation" or
        confirmation.get("mode") != "dialog"):
        raise ValueError("real native approval dialog not verified")
    if choice == "deny":
        if (result.get("status") != "rejected" or
            confirmation.get("approved") is not False or
            "operator declined elevated request" not in confirmation.get("message", "")):
            raise ValueError("explicit rejection was not proved")
    else:
        if (result.get("status") != "completed" or
            result.get("exit_code") != 0 or
            confirmation.get("approved") is not True or
            result.get("write_scope", {}).get("effective") != "trusted_operation"):
            raise ValueError("approved trusted operation did not complete")
        output = json.loads(result.get("stdout_text", ""))
        if output.get("kind") != MARKER or output.get("mutations_performed") is not False:
            raise ValueError("approved inspector output was not read-only")


def main(args: list[str]) -> int:
    if len(args) != 6 or args[1] not in ("create", "verify"):
        print("usage: verify_v6_approval.py create|verify FILE ID CWD|RESULT deny|allow", file=sys.stderr)
        return 2
    cmd, path, rid, arg, choice = args[1:]
    if choice not in ("deny", "allow"):
        return 2
    try:
        if cmd == "create":
            if not rid.startswith("v6-approval-") or not Path(arg).is_dir():
                raise ValueError("invalid staging approval request")
            Path(path).write_text(json.dumps(request(rid, arg), sort_keys=True,
                                             separators=(",", ":")), encoding="utf-8")
        else:
            raw = Path(path).read_bytes()
            result = json.loads(Path(arg).read_text(encoding="utf-8"))
            verify(raw, result, rid, choice)
            print("NATIVE_APPROVAL_" + choice.upper() + "_VERIFIED=1")
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        print("NATIVE_APPROVAL_NOT_VERIFIED: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
