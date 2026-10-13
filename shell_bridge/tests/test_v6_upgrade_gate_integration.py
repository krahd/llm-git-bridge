"""Ensure the explicit v6 upgrade uses the actual merged pre-stop module.

This test intentionally fails against an obsolete base tree missing PR #57.
"""
import datetime as dt
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from v6_to_v6_preflight import assess


class IntegratedV6HandoverPrestopTests(unittest.TestCase):
    def test_live_conflicting_ownership_and_indeterminate_journal_cannot_admit_stop(self):
        production = {
            "bridge_instance_id": "prod-instance",
            "drive_root_folder_id": "prod-root",
            "requests_folder_id": "prod-req",
            "results_folder_id": "prod-results",
            "state_dir": "/Users/test/.local/state/prod",
        }
        loaded = [
            {"label": "net.laurenzo.local-executor-bridge", "pid": "1786",
             "running": True, "state": "verified", **production},
            {"label": "io.llm-git-bridge.daemon", "pid": "1738",
             "running": True, "state": "verified", **production},
        ]
        inventory = {
            "kind": "loaded_bridge_ownership",
            "loaded": loaded,
            "mutations_performed": False,
            "safe_to_stage": False,
            "problems": ["shared mailbox", "shared state_dir"],
            "active_collisions": ["duplicate running owner"],
        }
        health = {
            "bridge_version": "6",
            "bridge_instance_id": production["bridge_instance_id"],
            "drive_root_folder_id": production["drive_root_folder_id"],
            "requests_folder_id": production["requests_folder_id"],
            "results_folder_id": production["results_folder_id"],
            "publisher_pid": 1786,
            "updated_at": "2026-10-12T18:00:00+00:00",
            "active_requests": [],
            "pending_approvals": [],
            "state": {"started_without_finished": 0},
        }
        report = assess(
            inventory,
            "net.laurenzo.local-executor-bridge",
            "net.laurenzo.local-executor-bridge-v6-next",
            production, production, health,
            journal_verified=False,
            pending_mailbox_files=[],
            candidate_source_commit="a" * 40,
            candidate_manifest_verified=True,
            now=dt.datetime(2026, 10, 12, 18, tzinfo=dt.timezone.utc),
        )
        self.assertIn("ownership_not_exclusive", report["blockers"])
        self.assertIn("durable_journal_not_fully_verified", report["blockers"])
        self.assertFalse(report["prestop_evidence_consistent"])
        self.assertFalse(report["activation_authorized"])
        self.assertFalse(report["retirement_authorized"])
        self.assertFalse(report["mutations_performed"])


if __name__ == "__main__":
    unittest.main()
