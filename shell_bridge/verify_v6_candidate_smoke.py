#!/usr/bin/env python3
"""Verify a bounded, harmless, read-only staging smoke transaction."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


class SmokeVerificationError(Exception):
    pass


def verify(request_bytes: bytes, result: dict, rid: str) -> None:
    checks = {
        "protocol": 1,
        "kind": "result",
        "id": rid,
        "status": "completed",
        "exit_code": 0,
        "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
        "filesystem_sandboxed": True,
        "network_sandboxed": True,
    }
    for key, expected in checks.items():
        if result.get(key) != expected or (
            isinstance(expected, int) and not isinstance(expected, bool)
            and isinstance(result.get(key), bool)):
            raise SmokeVerificationError("staging smoke result mismatch: " + key)
    plan = result.get("write_scope")
    if not isinstance(plan, dict) or plan.get("effective") != "read_only":
        raise SmokeVerificationError("staging smoke did not run in read-only scope")
    if "ISOLATED_V6_SMOKE_OK" not in result.get("stdout_text", ""):
        raise SmokeVerificationError("staging smoke output marker missing")


def main(args: list[str]) -> int:
    if len(args) != 4:
        print("usage: verify_v6_candidate_smoke.py REQUEST RESULT ID", file=sys.stderr)
        return 2
    try:
        raw = Path(args[1]).read_bytes()
        result = json.loads(Path(args[2]).read_text(encoding="utf-8"))
        verify(raw, result, args[3])
    except (OSError, ValueError, TypeError, SmokeVerificationError) as exc:
        print("CANDIDATE_SMOKE_NOT_VERIFIED: " + str(exc), file=sys.stderr)
        return 1
    print("ISOLATED_V6_SMOKE_VERIFIED=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
