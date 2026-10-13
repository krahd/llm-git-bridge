#!/usr/bin/env python3
"""Verify there are no unresolved operator-approval markers in a v6 journal.

Read only. A linked, malformed or unreadable approval queue is NOT empty.
Do not read or print approval text, shell commands, credentials or decisions.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys


class ApprovalQueueHold(ValueError):
    pass


def verify(state: Path, *, limit: int = 4096) -> None:
    if not state.is_absolute() or state.is_symlink() or not state.is_dir():
        raise ApprovalQueueHold("production state is missing or linked")
    approvals = state / "approvals"
    pending = approvals / "pending"
    # An old bridge may never have created the optional approval directory.
    # Such absence is fine only if it is genuinely absent (not a dangling link).
    for path in (approvals, pending):
        try:
            info = path.lstat()
        except FileNotFoundError:
            if path.is_symlink():
                raise ApprovalQueueHold("approval directory is a broken link")
            return
        except OSError as exc:
            raise ApprovalQueueHold("approval queue metadata unavailable") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise ApprovalQueueHold("approval queue is linked or not a directory")
    try:
        with os.scandir(pending) as entries:
            for count, entry in enumerate(entries, start=1):
                if count > limit:
                    raise ApprovalQueueHold("approval queue exceeds bounded listing")
                # Even an unexpected file is unresolved evidence. Do not
                # interpret expired records or malformed JSON as clearance.
                raise ApprovalQueueHold("operator approval queue is not empty")
    except OSError as exc:
        raise ApprovalQueueHold("approval queue cannot be listed") from exc


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: verify_pending_approvals.py ABSOLUTE_STATE_DIR", file=sys.stderr)
        return 2
    try:
        verify(Path(argv[1]))
    except ApprovalQueueHold as exc:
        print("APPROVAL_QUEUE_HOLD: " + str(exc), file=sys.stderr)
        return 1
    print("PENDING_APPROVALS_CLEAR=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
