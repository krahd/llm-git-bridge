#!/usr/bin/env python3
"""Read-only fail-closed local journal gate before production mailbox handover.

A FINISHED record with status=indeterminate is *not* evidence of a safe,
replayable effect. The production heartbeat may only count STARTED without
FINISHED; the actual local journal must be inspected separately.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

from bridge import _result_state_counts


class UnresolvedJournalError(ValueError):
    pass


def verify(state: Path) -> dict:
    if state.is_symlink() or not state.is_dir():
        raise UnresolvedJournalError("state path is missing or symlinked")
    journal = state / "requests"
    if journal.is_symlink():
        raise UnresolvedJournalError("journal path is a symlink")
    if journal.exists():
        if not journal.is_dir():
            raise UnresolvedJournalError("journal is not a directory")
        try:
            # Bound this safety check; a giant journal is not evidence of
            # quiescence, and never truncate a listing to claim success.
            with __import__("os").scandir(journal) as entries:
                count = sum(1 for _ in entries)
        except OSError as exc:
            raise UnresolvedJournalError("journal cannot be enumerated") from exc
        if count > 10000:
            raise UnresolvedJournalError("journal has too many entries for bounded audit")
    counters = _result_state_counts({"state_dir": str(state)})
    if any(counters.get(key) != 0 for key in
           ("started_without_finished", "indeterminate", "unverifiable_finished")):
        raise UnresolvedJournalError(
            "journal contains unresolved, indeterminate or unreadable requests: " +
            json.dumps(counters, sort_keys=True))
    return counters


def main(args: list[str]) -> int:
    if len(args) != 2:
        print("usage: verify_quiescent_journal.py ABSOLUTE_STATE_DIR", file=sys.stderr)
        return 2
    path = Path(args[1])
    try:
        if not path.is_absolute():
            raise UnresolvedJournalError("journal path must be absolute")
        print("QUIESCENT_JOURNAL_VERIFIED=" + json.dumps(verify(path), sort_keys=True))
    except (UnresolvedJournalError, OSError, ValueError) as exc:
        print("JOURNAL_HANDOVER_HOLD: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
