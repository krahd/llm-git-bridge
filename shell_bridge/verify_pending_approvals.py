#!/usr/bin/env python3
"""Verify there are no unresolved operator-approval markers in a v6 journal.

Read only. A linked, malformed or unreadable approval queue is NOT empty.
Do not read or print approval text, shell commands, credentials or decisions.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys


class ApprovalQueueHold(ValueError):
    pass


def verify(state: Path, *, limit: int = 4096) -> None:
    if not state.is_absolute() or state.is_symlink():
        raise ApprovalQueueHold("production state is missing or linked")
    # Open each directory relative to a no-follow descriptor. A path that is
    # replaced by a symlink between lstat and scandir must never produce a
    # false "empty approvals" result.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    handles: list[int] = []
    try:
        handles.append(os.open(state, flags))
        try:
            handles.append(os.open("approvals", flags, dir_fd=handles[-1]))
        except FileNotFoundError:
            return  # bridge version with no approval queue
        try:
            handles.append(os.open("pending", flags, dir_fd=handles[-1]))
        except FileNotFoundError:
            return  # optional pending directory genuinely absent
        with os.scandir(handles[-1]) as entries:
            for count, entry in enumerate(entries, start=1):
                if count > limit:
                    raise ApprovalQueueHold("approval queue exceeds bounded listing")
                # An unknown, expired or malformed file remains an unsafe
                # pending record; never parse potentially sensitive content.
                raise ApprovalQueueHold("operator approval queue is not empty")
    except ApprovalQueueHold:
        raise
    except OSError as exc:
        raise ApprovalQueueHold("approval queue metadata is unverifiable") from exc
    finally:
        for fd in reversed(handles):
            os.close(fd)

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
