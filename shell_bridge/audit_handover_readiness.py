#!/usr/bin/env python3
"""One-pass, read-only Mac handover audit, including every loaded bridge journal.

An ownership conflict must not short-circuit journal inspection. This report
does NOT establish exclusive flock ownership, remote mailbox quiescence, live
installed build identity, approval acceptance, or cutover authorization.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from bridge_lock_observer import inspect as inspect_lock_descriptors
from bridge_service_ownership import inspect_services
from verify_quiescent_journal import UnresolvedJournalError, verify as verify_journal


MAX_LOADED_SERVICES = 64
MAX_UNIQUE_STATES = 64


def audit_inventory(inventory: dict, home: Path, *,
                    verify=verify_journal, inspect_locks=inspect_lock_descriptors) -> dict:
    """Aggregate bounded local evidence without mutating or granting authority."""
    loaded = inventory.get("loaded")
    if not isinstance(loaded, list) or len(loaded) > MAX_LOADED_SERVICES:
        raise ValueError("loaded service inventory is invalid or exceeds audit limit")
    state_base = home / ".local" / "state"
    roots: set[Path] = set()
    journal_problems = []
    for record in loaded:
        if not isinstance(record, dict) or record.get("state") != "verified":
            journal_problems.append("unverifiable service identity")
            continue
        raw = record.get("state_dir")
        if not isinstance(raw, str):
            journal_problems.append("missing durable state directory")
            continue
        path = Path(raw)
        # The inspector validates config paths but may report an external
        # absolute state directory. Never traverse arbitrary paths on audit.
        if not path.is_absolute() or state_base not in path.parents:
            journal_problems.append("durable state outside allowed audit root")
            continue
        ancestor = path
        linked = False
        while ancestor != home:
            if ancestor.is_symlink():
                linked = True
                break
            ancestor = ancestor.parent
        if linked:
            journal_problems.append("linked state directory or ancestor")
            continue
        roots.add(path)
    if len(roots) > MAX_UNIQUE_STATES:
        raise ValueError("unique durable state directories exceed audit limit")
    journals = []
    for state in sorted(roots):
        try:
            counters = verify(state)
            journals.append({"state_dir": str(state), "status": "verified",
                             "counters": counters})
        except (UnresolvedJournalError, OSError, ValueError) as exc:
            journals.append({"state_dir": str(state), "status": "hold",
                             "reason": str(exc)[:400]})
    try:
        lock_evidence = inspect_locks(inventory)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        lock_evidence = {"kind": "bridge_open_lock_file_evidence",
                         "lock_evidence": [], "exclusive_lock_proved": False,
                         "reason": "lock descriptor inspection unavailable"}
    # Never call a descriptor report proof of exclusive OS flock ownership.
    lock_evidence["exclusive_lock_proved"] = False
    owner_problems = inventory.get("problems", [])
    if not isinstance(owner_problems, list):
        owner_problems = ["invalid ownership problem list"]
    local_holds = bool(owner_problems or journal_problems or
                       any(item.get("status") != "verified" for item in journals) or
                       lock_evidence.get("reason"))
    return {
        "local_holds_detected": local_holds,
        "schema": 1,
        "kind": "bridge_local_handover_readonly_audit",
        "running_claimants": inventory.get("running_claimants", []),
        "parked_loaded": inventory.get("parked_loaded", []),
        "ownership_problems": owner_problems,
        "loaded_services_count": len(loaded),
        "unique_journal_dirs_checked": len(journals),
        "journal_problems": journal_problems,
        "journals": journals,
        "lock_descriptor_evidence": lock_evidence,
        "remote_mailbox_quiescence_verified": False,
        "exclusive_lock_proved": False,
        "installed_build_verified": False,
        "rollback_verified": False,
        "retirement_authorized": False,
        "mutations_performed": False,
    }


def main() -> int:
    try:
        services = subprocess.run(["/bin/launchctl", "list"],
                                  capture_output=True, text=True, timeout=8)
        processes = subprocess.run(["/bin/ps", "-axo", "pid=,command="],
                                   capture_output=True, text=True, timeout=8)
        if services.returncode or processes.returncode:
            raise ValueError("cannot inventory local launchd and process state")
        report = audit_inventory(
            inspect_services(Path.home(), services.stdout, processes.stdout),
            Path.home(),
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print("LOCAL_HANDOVER_AUDIT_UNAVAILABLE: " + type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    # A syntactically successful diagnostic is not a clean local audit.
    # Code 3 indicates actionable conflicts/holds; code 0 only means
    # no *local* holds. Neither code authorizes service mutation.
    return 3 if report["local_holds_detected"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
