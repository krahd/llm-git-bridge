#!/usr/bin/env python3
"""Read-only fail-closed local journal gate before production mailbox handover.

A FINISHED record with status=indeterminate is *not* evidence of a safe,
replayable effect. The production heartbeat may only count STARTED without
FINISHED; the actual local journal must be inspected separately.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
import sys
import time


class UnresolvedJournalError(ValueError):
    pass


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_RESULT_BYTES = 48 * 1024 * 1024
_MAX_TOTAL_BYTES = 256 * 1024 * 1024
# Permit large, established production journals without claiming an incomplete
# audit is safe. Each journal is fully checked or held at a bounded limit.
_MAX_JOURNAL_ENTRIES = 100000
_MAX_JOURNAL_AUDIT_SECONDS = 120


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
    # Completed shells always persist STARTED before spawning. A separate
    # request can have a completed result without STARTED only when it was
    # deduplicated against a verified completed logical operation. Otherwise
    # losing STARTED erases the only evidence that execution was admitted.
    started = folder / "started.json"
    if status == "completed" and not started.exists() and not started.is_symlink():
        source_id = result_data.get("original_request_id")
        source_sha = result_data.get("original_result_sha256")
        operation_id = result_data.get("logical_operation_id")
        if (result_data.get("replayed") is not True
                or not isinstance(source_id, str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", source_id)
                or source_id in (".", "..", folder.name)
                or not isinstance(operation_id, str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", operation_id)
                or not isinstance(source_sha, str)
                or _SHA256.fullmatch(source_sha) is None):
            raise UnresolvedJournalError("completed receipt has no STARTED or valid replay proof")
        source = folder.parent / source_id
        source_result = source / "result.json"
        source_started = source / "started.json"
        source_finished = source / "finished.json"
        if (source.is_symlink() or not source.is_dir()
                or any(p.is_symlink() or not p.is_file()
                       for p in (source_result, source_started, source_finished))):
            raise UnresolvedJournalError("completed replay source receipt is missing or linked")
        try:
            if source_result.stat().st_size > _MAX_RESULT_BYTES:
                raise UnresolvedJournalError("completed replay source exceeds bounded audit budget")
            original_raw = source_result.read_bytes()
            if hashlib.sha256(original_raw).hexdigest() != source_sha:
                raise UnresolvedJournalError("completed replay source result SHA disagrees")
            original = json.loads(original_raw)
        except UnresolvedJournalError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise UnresolvedJournalError("completed replay source result is unreadable") from exc
        if (not isinstance(original, dict)
                or original.get("status") != "completed"
                or original.get("id") != source_id
                or original.get("kind") != "result"
                or original.get("protocol") != 1):
            raise UnresolvedJournalError("completed replay source is not a completed original")
        # The source STARTED, FINISHED and executed request are independently
        # checked when every journal entry is visited by verify().
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
    counters = {"started_without_finished": 0, "finished": 0,
                "indeterminate": 0, "unverifiable_finished": 0}
    if journal.exists():
        if not journal.is_dir():
            raise UnresolvedJournalError("journal is not a directory")
        remaining = _MAX_TOTAL_BYTES
        started_at = time.monotonic()
        try:
            # One streaming pass avoids materialising a large collection of
            # path objects or reading every FINISHED marker a second time.
            # Never truncate the directory to claim safe quiescence.
            with os.scandir(journal) as entries:
                for count, entry in enumerate(entries, start=1):
                    if count > _MAX_JOURNAL_ENTRIES:
                        raise UnresolvedJournalError(
                            "journal exceeds bounded entry audit limit")
                    if count % 128 == 0 and (
                            time.monotonic() - started_at > _MAX_JOURNAL_AUDIT_SECONDS):
                        raise UnresolvedJournalError(
                            "journal exceeds bounded wall-clock audit limit")
                    if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                        raise UnresolvedJournalError(
                            "journal contains non-directory or linked entry")
                    folder = Path(entry.path)
                    started = folder / "started.json"
                    finished = folder / "finished.json"
                    # Verify receipt pairing first to retain a specific
                    # missing-FINISHED or linked-receipt diagnosis. Then
                    # reject dangling STARTED evidence even if no result was
                    # ever persisted. Never infer a replayable effect.
                    remaining = _verify_completed_receipt_pair(folder, remaining)
                    if started.is_symlink():
                        raise UnresolvedJournalError("STARTED marker is symlinked")
                    if started.exists() and not finished.exists():
                        raise UnresolvedJournalError(
                            "journal contains unresolved STARTED without FINISHED")
                    if finished.exists():
                        counters["finished"] += 1
        except UnresolvedJournalError:
            raise
        except OSError as exc:
            raise UnresolvedJournalError("journal cannot be fully enumerated") from exc
        if time.monotonic() - started_at > _MAX_JOURNAL_AUDIT_SECONDS:
            raise UnresolvedJournalError("journal exceeds bounded wall-clock audit limit")
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
