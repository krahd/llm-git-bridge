#!/usr/bin/env python3
"""Fail if a release acceptance request has zero or multiple Drive results.

This checks *listing* evidence, not a path read: Google Drive permits more than
one file of an identical name. A successful rclone copyto is not enough.
"""
from __future__ import annotations

import json
import re
import sys

ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")


def verify(rid: str, items: object) -> str:
    if not isinstance(rid, str) or not ID.fullmatch(rid):
        raise ValueError("invalid acceptance request ID")
    if not isinstance(items, list) or len(items) > 50:
        raise ValueError("result listing invalid or exceeds bounded scope")
    names = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("malformed result listing row")
        name = item.get("Name")
        if not isinstance(name, str) or not name:
            raise ValueError("invalid result filename")
        names.append(name)
    expected = rid + ".json"
    if any(name != expected for name in names):
        raise ValueError("result listing was not restricted to exact request filename")
    if len(names) != 1:
        raise ValueError("expected exactly one Drive result file, found " + str(len(names)))
    if items[0].get("IsDir") is not False or not isinstance(items[0].get("ID"), str) or not items[0]["ID"]:
        raise ValueError("result file missing immutable Drive ID")
    return items[0]["ID"]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: verify_unique_result.py REQUEST_ID", file=sys.stderr)
        return 2
    try:
        source = sys.stdin.read(65537)
        if len(source) > 65536:
            raise ValueError("unbounded result listing")
        drive_id = verify(argv[1], json.loads(source))
    except (ValueError, TypeError, UnicodeError) as exc:
        print("DRIVE_RESULT_CARDINALITY_HOLD: " + str(exc), file=sys.stderr)
        return 1
    print("UNIQUE_DRIVE_RESULT_ID=" + drive_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
