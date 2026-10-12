#!/usr/bin/env python3
"""Read-only fail-closed local journal gate before production mailbox handover.

A FINISHED record with status=indeterminate is *not* evidence of a safe,
replayable effect. The production heartbeat may only count STARTED without
FINISHED; the actual local journal must be inspected separately.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
import sys

from bridge import _result_state_counts


class UnresolvedJournalError(ValueError):
    pass


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_RESULT_BYTES = 48 * 1024 * 1024
_MAX_TOTAL_BYTES = 256 * 1024 * 1024


def _verify_completed_receipt_pair(folder: Path, budget: int) -> int:
    """A FINISHED marker alone cannot attest to an immutable local result."""
    finished = folder / "finished.json"
    result = folder / "result.json"
    active = folder / "active.json"
    if active.is_symlink() or active.exists():
        raise UnresolvedJournalError("journal contains stale active process evidence")
    if finished.is_symlink() or result.is_symlink():
        raise UnresolvedJournalError("journal receipt path is symlinked")
    if not finished.exists():
        if result.exists():
            raise UnresolvedJournalError("local result has no durable FINISHED marker")
        return budget
    if not finished.is_file() or not result.is_file():
        raise UnresolvedJournalError("FINISHED marker has no matching local result")
    try:
        meta = result.stat()
        finish_stat = finished.stat()
        if (meta.st_size > _MAX_RESULT_BYTES or
                meta.st_size + finish_stat.st_size > budget or
                finish_stat.st_size > 65536):
            raise UnresolvedJournalError("completed receipt exceeds bounded audit budget")
        finish_data = json.loads(finished.read_text(encoding="utf-8"))
        result_data = json.loads(result.read_text(encoding="utf-8"))
    except UnresolvedJournalError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise UnresolvedJournalError("FINISHED/result pair is unreadable") from exc
    if not isinstance(finish_data, dict) or not isinstance(result_data, dict):
        raise UnresolvedJournalError("FINISHED/result pair is malformed")
    status = finish_data.get("status")
    sha = finish_data.get("request_sha256")
    if status == "indeterminate" or result_data.get("status") == "indeterminate":
        raise UnresolvedJournalError("journal contains indeterminate completion")
    if (status not in ("completed", "rejected") or
            not isinstance(sha, str) or _SHA256.fullmatch(sha) is None or
            result_data.get("status") != status or
            result_data.get("request_sha256") != sha or
            result_data.get("id") != folder.name or
            result_data.get("protocol") != 1 or
            result_data.get("kind") != "result"):
        raise UnresolvedJournalError("FINISHED metadata and local result disagree")
    started = folder / "started.json"
    if started.is_symlink():
        raise UnresolvedJournalError("STARTED marker is symlinked")
    if started.exists():
        if not started.is_file() or started.stat().st_size > 65536:
            raise UnresolvedJournalError("STARTED marker is not a bounded regular file")
        try:
            original = json.loads(started.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise UnresolvedJournalError("STARTED identity is unreadable") from exc
        if not isinstance(original, dict) or original.get("request_sha256") != sha:
            raise UnresolvedJournalError("STARTED and FINISHED request identities disagree")
        # A STARTED marker means this request may have produced side effects.
        # Do not certify safe handover unless the immutable original request
        # bytes are still present and match its execution/FINISHED identity.
        raw_path = folder / "request.json"
        if raw_path.is_symlink() or not raw_path.is_file():
            raise UnresolvedJournalError("executed request payload is absent or symlinked")
        try:
            size = raw_path.stat().st_size
            if size > 2 * 1024 * 1024 or size > budget - meta.st_size - finish_stat.st_size:
                raise UnresolvedJournalError("executed request exceeds bounded audit budget")
            raw = raw_path.read_bytes()
        except UnresolvedJournalError:
            raise
        except OSError as exc:
            raise UnresolvedJournalError("executed request payload cannot be read") from exc
        if hashlib.sha256(raw).hexdigest() != sha:
            raise UnresolvedJournalError("executed request bytes disagree with STARTED/FINISHED")
        budget -= len(raw)
    return budget - meta.st_size - finish_stat.st_size


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
        remaining = _MAX_TOTAL_BYTES
        for folder in journal.iterdir():
            if folder.is_symlink() or not folder.is_dir():
                raise UnresolvedJournalError("journal contains non-directory or linked entry")
            remaining = _verify_completed_receipt_pair(folder, remaining)
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
