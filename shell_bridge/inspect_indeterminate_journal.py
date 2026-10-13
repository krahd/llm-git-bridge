#!/usr/bin/env python3
"""Read-only bounded identification of unresolved local bridge request receipts.

Only emits request directory names and controlled diagnostic labels/booleans.
Never prints commands, cwd, stdin, result output, credentials, or raw payloads.
This is forensic triage, not permission to replay requests or transfer owners.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import time

_MAX_REQUEST_DIRS = 100000
_MAX_SCAN_SECONDS = 120
_MAX_TOTAL_READ_BYTES = 256 * 1024 * 1024
_MAX_FINISHED_BYTES = 65536
_MAX_RESULT_BYTES = 1024 * 1024
_MAX_EXAMPLES = 40
_TERMINAL = {"completed", "rejected", "indeterminate"}


class InventoryUnavailable(ValueError):
    pass


def _status(path: Path, max_bytes: int, remaining: int) -> tuple[str, int]:
    """Read only bounded status metadata, with no arbitrary text in errors."""
    if path.is_symlink():
        return "invalid", remaining
    try:
        # Open without following symlinks, including if a marker changes
        # between directory inspection and the read-only evidence pass.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            meta = os.fstat(handle.fileno())
            if not stat.S_ISREG(meta.st_mode):
                return "invalid", remaining
            if meta.st_size > max_bytes or meta.st_size > remaining:
                return "unverifiable", remaining
            raw = handle.read(meta.st_size + 1)
            if len(raw) != meta.st_size:
                return "unverifiable", remaining
        remaining -= len(raw)
        record = json.loads(raw.decode("utf-8"))
        if not isinstance(record, dict):
            return "invalid", remaining
        value = record.get("status")
        return (value if value in _TERMINAL else "invalid"), remaining
    except (OSError, UnicodeError, ValueError, TypeError):
        return "invalid", remaining


def inspect(state: Path) -> dict:
    if not state.is_absolute() or state.is_symlink() or not state.is_dir():
        raise InventoryUnavailable("state directory is absent, nonabsolute or symlinked")
    journal = state / "requests"
    if journal.is_symlink() or not journal.is_dir():
        raise InventoryUnavailable("request journal is missing, linked or not a directory")
    report = {
        "schema": 1,
        "kind": "bridge_unresolved_request_inventory",
        "state_dir": str(state),
        "scanned": 0,
        "issue_count": 0,
        "indeterminate_count": 0,
        "started_without_finished_count": 0,
        "active_marker_count": 0,
        "unverifiable_count": 0,
        "examples": [],
        "examples_truncated": False,
        "complete_scan": False,
        "handover_authorized": False,
        "mutations_performed": False,
    }
    budget = _MAX_TOTAL_READ_BYTES
    started_at = time.monotonic()
    try:
        with os.scandir(journal) as entries:
            for entry in entries:
                report["scanned"] += 1
                if (report["scanned"] > _MAX_REQUEST_DIRS or
                        time.monotonic() - started_at > _MAX_SCAN_SECONDS):
                    raise InventoryUnavailable("request inventory exceeds bounded scan limits")
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    raise InventoryUnavailable("non-directory or symlinked journal entry")
                folder = Path(entry.path)
                started = folder / "started.json"
                finished = folder / "finished.json"
                result = folder / "result.json"
                active = folder / "active.json"
                # No marker contents/commands are echoed to the caller.
                evidence = {
                    "request_id": entry.name,
                    "started_present": started.exists() or started.is_symlink(),
                    "finished_present": finished.exists() or finished.is_symlink(),
                    "result_present": result.exists() or result.is_symlink(),
                    "active_present": active.exists() or active.is_symlink(),
                }
                labels: list[str] = []
                for marker in (started, finished, result, active):
                    if marker.is_symlink():
                        labels.append("linked_marker")
                        break
                if evidence["finished_present"]:
                    fs, budget = _status(finished, _MAX_FINISHED_BYTES, budget)
                    evidence["finished_status"] = fs
                    if fs in {"invalid", "unverifiable"}:
                        labels.append("unverifiable_finished")
                if evidence["result_present"]:
                    rs, budget = _status(result, _MAX_RESULT_BYTES, budget)
                    evidence["result_status"] = rs
                    if rs in {"invalid", "unverifiable"}:
                        labels.append("unverifiable_result")
                fs = evidence.get("finished_status")
                rs = evidence.get("result_status")
                if fs == "indeterminate" or rs == "indeterminate":
                    labels.append("indeterminate")
                    report["indeterminate_count"] += 1
                if evidence["started_present"] and not evidence["finished_present"]:
                    labels.append("started_without_finished")
                    report["started_without_finished_count"] += 1
                if evidence["active_present"]:
                    labels.append("active_marker")
                    report["active_marker_count"] += 1
                if ((evidence["finished_present"] != evidence["result_present"]) or
                        (fs is not None and rs is not None and fs != rs)):
                    labels.append("receipt_pair_mismatch")
                if any(label in labels for label in
                       ("linked_marker", "unverifiable_finished",
                        "unverifiable_result", "receipt_pair_mismatch")):
                    report["unverifiable_count"] += 1
                if labels:
                    report["issue_count"] += 1
                    evidence["labels"] = sorted(set(labels))
                    if len(report["examples"]) < _MAX_EXAMPLES:
                        report["examples"].append(evidence)
                    else:
                        report["examples_truncated"] = True
    except (OSError, ValueError) as exc:
        if isinstance(exc, InventoryUnavailable):
            raise
        raise InventoryUnavailable("unable to complete local journal inventory") from exc
    if time.monotonic() - started_at > _MAX_SCAN_SECONDS:
        raise InventoryUnavailable("request inventory exceeds bounded scan limits")
    report["complete_scan"] = True
    return report


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: inspect_indeterminate_journal.py ABSOLUTE_STATE_DIR", file=sys.stderr)
        return 2
    try:
        print(json.dumps(inspect(Path(argv[1])), sort_keys=True))
    except InventoryUnavailable as exc:
        # Avoid printing local data even when inspection fails.
        print("LOCAL_UNRESOLVED_INVENTORY_HOLD: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
