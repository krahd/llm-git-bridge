#!/usr/bin/env python3
"""Fail-closed v6-to-v6 *pre-stop* gate for a future guarded controller.

This module has no service, filesystem, or network mutation. A green result
means only that the supplied local/remote snapshots meet the pre-stop gates:
it never authorizes bootout, bootstrap, rollback, request replay or retirement.
The eventual controller must gather fresh primary evidence itself and repeat
drain + journal checks after the source is stopped.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
import re

_SHA = re.compile(r"[a-f0-9]{40}\Z")
_ID_FIELDS = (
    "bridge_instance_id", "drive_root_folder_id",
    "requests_folder_id", "results_folder_id", "state_dir",
)


def assess(inventory: dict, source_label: str, candidate_label: str,
           source_config: dict, candidate_config: dict, health: dict,
           *, journal_verified: bool = False,
           pending_mailbox_files: list[str] | None = None,
           candidate_source_commit: str | None = None,
           candidate_manifest_verified: bool = False,
           now: dt.datetime | None = None) -> dict:
    """Classify *pre-stop* evidence. No returned field authorizes mutations."""
    blockers: set[str] = set()

    if (not isinstance(source_label, str) or not source_label or
            not isinstance(candidate_label, str) or not candidate_label or
            source_label == candidate_label):
        blockers.add("invalid_distinct_service_labels")
    if not isinstance(inventory, dict) or inventory.get("kind") != "loaded_bridge_ownership":
        blockers.add("invalid_live_inventory")
        inventory = {}
    if (inventory.get("mutations_performed") is not False or
            inventory.get("safe_to_stage") is not True or
            not isinstance(inventory.get("loaded"), list) or
            inventory.get("problems") or inventory.get("active_collisions")):
        blockers.add("ownership_not_exclusive")
    loaded = inventory.get("loaded", [])
    if not isinstance(loaded, list) or any(
            not isinstance(item, dict) or item.get("state") != "verified"
            for item in loaded):
        blockers.add("unverifiable_loaded_services")
        loaded = []
    source = [x for x in loaded if x.get("label") == source_label]
    candidate = [x for x in loaded if x.get("label") == candidate_label]
    if len(source) != 1 or source[0].get("running") is not True or (
            not isinstance(source[0].get("pid"), str) or
            not source[0]["pid"].isdecimal()):
        blockers.add("source_not_single_running_service")
    if candidate:
        blockers.add("candidate_already_loaded")

    if not isinstance(source_config, dict):
        source_config = {}
    if not isinstance(candidate_config, dict):
        candidate_config = {}
    for key in _ID_FIELDS:
        a, b = source_config.get(key), candidate_config.get(key)
        if not isinstance(a, str) or not a or not isinstance(b, str) or a != b:
            blockers.add("candidate_identity_or_journal_mismatch")
    if source:
        for key in _ID_FIELDS:
            if source[0].get(key) != source_config.get(key):
                blockers.add("source_launchd_config_mismatch")
        # Loaded consumers on *any* mailbox-sharing identity are not safe to
        # dismiss as parked; they may restart after the source is stopped.
        for other in loaded:
            if other is source[0]:
                continue
            if any(other.get(key) == source_config.get(key)
                   for key in _ID_FIELDS if source_config.get(key)):
                blockers.add("other_loaded_mailbox_or_state_claimant")
    if not isinstance(health, dict):
        health = {}
    if (str(health.get("bridge_version")) != "6" or
            health.get("bridge_instance_id") != source_config.get("bridge_instance_id") or
            any(health.get(k) != source_config.get(k) for k in
                ("drive_root_folder_id", "requests_folder_id", "results_folder_id"))):
        blockers.add("source_is_not_verified_v6")
    if not source or not isinstance(health.get("publisher_pid"), int) or (
            isinstance(health.get("publisher_pid"), bool) or
            str(health.get("publisher_pid")) != source[0].get("pid")):
        blockers.add("source_health_pid_disagrees")
    try:
        stamp = dt.datetime.fromisoformat(
            health["updated_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("timezone required")
        age = ((now or dt.datetime.now(dt.timezone.utc)) - stamp).total_seconds()
        if not -30 <= age <= 180:
            raise ValueError("stale or future")
    except (KeyError, TypeError, ValueError, AttributeError):
        blockers.add("source_health_not_fresh")
    if health.get("active_requests") != [] or health.get("pending_approvals", []) != []:
        blockers.add("source_has_active_requests_or_approvals")
    if not isinstance(health.get("state"), dict) or (
            health["state"].get("started_without_finished") != 0):
        blockers.add("source_health_unresolved_state")
    if journal_verified is not True:
        blockers.add("durable_journal_not_fully_verified")
    # Do not accept "not checked" as "empty", or an unbounded string as a
    # request listing. Remote mailbox enumeration must be independently
    # refreshed immediately before and after the stop transition.
    if not isinstance(pending_mailbox_files, list) or (
            pending_mailbox_files or not all(isinstance(x, str)
                                             for x in pending_mailbox_files)):
        blockers.add("remote_requests_not_proven_empty")
    if not isinstance(candidate_source_commit, str) or not _SHA.fullmatch(
            candidate_source_commit):
        blockers.add("candidate_revision_not_pinned")
    if candidate_manifest_verified is not True:
        blockers.add("candidate_install_integrity_unverified")
    if any(not isinstance(item, dict) or not item.get("state_dir")
           for item in loaded):
        blockers.add("unverifiable_loaded_services")
    return {
        "schema": 1,
        "kind": "v6_to_v6_prestop_gate",
        "blockers": sorted(blockers),
        "prestop_evidence_consistent": not blockers,
        "remote_quiescence_after_stop_verified": False,
        "exclusive_takeover_verified": False,
        "rollback_verified": False,
        "activation_authorized": False,
        "retirement_authorized": False,
        "mutations_performed": False,
    }
