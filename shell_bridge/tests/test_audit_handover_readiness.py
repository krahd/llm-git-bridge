import tempfile
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_handover_readiness import audit_inventory
from verify_quiescent_journal import UnresolvedJournalError


class LocalHandoverReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.root = self.home / ".local" / "state"
        self.root.mkdir(parents=True)
        self.prod = self.root / "prod"
        self.stage = self.root / "stage"

    def inventory(self):
        return {
            "loaded": [
                {"label": "production-a", "state": "verified",
                 "state_dir": str(self.prod), "running": True},
                {"label": "production-b", "state": "verified",
                 "state_dir": str(self.prod), "running": True},
                {"label": "stage-parked", "state": "verified",
                 "state_dir": str(self.stage), "running": False},
            ],
            "safe_to_stage": False,
            "problems": ["shared production state"],
            "running_claimants": [{"label": "production-a"}, {"label": "production-b"}],
            "parked_loaded": ["stage-parked"],
        }

    def test_conflicted_owners_do_not_skip_every_unique_journal(self):
        seen = []

        def verify(path):
            seen.append(path)
            if path == self.prod:
                raise UnresolvedJournalError("indeterminate work")
            return {"finished": 0, "started_without_finished": 0}

        report = audit_inventory(
            self.inventory(), self.home, verify=verify,
            inspect_locks=lambda _: {"lock_evidence": [], "exclusive_lock_proved": True},
        )
        self.assertEqual(set(seen), {self.prod, self.stage})
        self.assertEqual(len(seen), 2)
        self.assertEqual(report["unique_journal_dirs_checked"], 2)
        self.assertEqual(len([r for r in report["journals"] if r["status"] == "hold"]), 1)
        self.assertEqual(report["ownership_problems"], ["shared production state"])
        self.assertTrue(report["local_holds_detected"])
        self.assertFalse(report["exclusive_lock_proved"])
        self.assertFalse(report["lock_descriptor_evidence"]["exclusive_lock_proved"])
        self.assertFalse(report["remote_mailbox_quiescence_verified"])
        self.assertFalse(report["retirement_authorized"])
        self.assertFalse(report["mutations_performed"])

    def test_one_pass_audit_identifies_indeterminate_request_without_exposing_contents(self):
        requested = []
        def verify(path):
            if path == self.prod:
                raise UnresolvedJournalError("journal contains indeterminate completion")
            return {"finished": 12, "started_without_finished": 0}
        def forensic(path):
            requested.append(path)
            return {
                "complete_scan": True,
                "scanned": 12953,
                "issue_count": 2,
                "indeterminate_count": 1,
                "started_without_finished_count": 0,
                "examples_truncated": False,
                "examples": [
                    {"request_id": "v6-owner-audit-20261011-readonly-a2",
                     "labels": ["indeterminate"], "command": "NEVER_EXPOSE_COMMAND"},
                    {"request_id": "../SECRET_IN_DIR_NAME",
                     "labels": ["active_marker", "unverifiable_result"],
                     "oauth_token": "NEVER_EXPOSE_TOKEN"},
                ],
            }
        report = audit_inventory(
            self.inventory(), self.home, verify=verify,
            inspect_locks=lambda _: {"lock_evidence": []},
            inspect_unresolved=forensic,
        )
        self.assertEqual(requested, [self.prod])
        self.assertTrue(report["local_holds_detected"])
        self.assertFalse(report["retirement_authorized"])
        self.assertFalse(report["mutations_performed"])
        entry = next(item for item in report["journals"]
                     if item["state_dir"] == str(self.prod))
        details = entry["unresolved_inventory"]
        self.assertEqual(details["status"], "scanned")
        self.assertEqual(details["indeterminate_count"], 1)
        self.assertEqual(details["examples"][0]["request_id"],
                         "v6-owner-audit-20261011-readonly-a2")
        self.assertEqual(details["examples"][1]["request_id"], "<invalid-request-id>")
        self.assertFalse(details["replay_authorized"])
        import json
        data = json.dumps(report)
        for secret in ("NEVER_EXPOSE_COMMAND", "NEVER_EXPOSE_TOKEN",
                       "SECRET_IN_DIR_NAME"):
            self.assertNotIn(secret, data)

    def test_unavailable_forensic_pass_never_reclassifies_a_journal_hold(self):
        from inspect_indeterminate_journal import InventoryUnavailable
        def failed(_):
            raise InventoryUnavailable("unavailable")
        report = audit_inventory(
            self.inventory(), self.home,
            verify=lambda _: (_ for _ in ()).throw(UnresolvedJournalError("indeterminate")),
            inspect_locks=lambda _: {"lock_evidence": []},
            inspect_unresolved=failed,
        )
        self.assertTrue(report["local_holds_detected"])
        self.assertEqual(len(report["journals"]), 2)
        self.assertTrue(all(j["status"] == "hold" for j in report["journals"]))
        self.assertTrue(all(j["unresolved_inventory"]["status"] == "unavailable"
                            for j in report["journals"]))

    def test_unverified_and_outside_state_dirs_fail_closed_without_traversal(self):
        observed = []
        inventory = self.inventory()
        inventory["loaded"] += [
            {"state": "unverifiable", "state_dir": str(self.root / "unknown")},
            {"state": "verified", "state_dir": "/var/tmp/unrelated"},
            {"state": "verified", "state_dir": "relative"},
        ]
        result = audit_inventory(
            inventory, self.home,
            verify=lambda path: observed.append(path) or {},
            inspect_locks=lambda _: {},
        )
        self.assertEqual(set(observed), {self.prod, self.stage})
        self.assertEqual(len(result["journal_problems"]), 3)
        self.assertTrue(result["local_holds_detected"])
        self.assertFalse(result["retirement_authorized"])

    def test_clean_local_inventory_does_not_authorize_cutover(self):
        inventory = self.inventory()
        inventory["problems"] = []
        inventory["safe_to_stage"] = True
        inventory["loaded"] = inventory["loaded"][:1]
        result = audit_inventory(
            inventory, self.home,
            verify=lambda _: {"finished": 0, "started_without_finished": 0},
            inspect_locks=lambda _: {"lock_evidence": [], "exclusive_lock_proved": True},
        )
        self.assertFalse(result["local_holds_detected"])
        self.assertFalse(result["exclusive_lock_proved"])
        self.assertFalse(result["retirement_authorized"])

    def test_false_owner_safety_flag_is_a_hold_even_without_details(self):
        inventory = self.inventory()
        inventory["problems"] = []
        report = audit_inventory(inventory, self.home,
                                 verify=lambda _: {"finished": 0},
                                 inspect_locks=lambda _: {"lock_evidence": []})
        self.assertTrue(report["local_holds_detected"])

    def test_unrecognized_open_lock_descriptor_is_a_hold(self):
        inventory = self.inventory()
        inventory["problems"] = []
        inventory["safe_to_stage"] = True
        report = audit_inventory(
            inventory, self.home, verify=lambda _: {"finished": 0},
            inspect_locks=lambda _: {"lock_evidence": [{
                "lock_evidence_state": "open_descriptors_observed",
                "unrecognized_open_pids": ["9876"],
            }]},
        )
        self.assertTrue(report["local_holds_detected"])

    def test_local_hold_requires_nonzero_exit(self):
        import audit_handover_readiness as module
        from unittest.mock import patch
        from io import StringIO
        inventory = self.inventory()
        with patch.object(module.subprocess, "run") as run, \
             patch.object(module, "inspect_services", return_value=inventory), \
             patch.object(module, "audit_inventory", return_value={
                 "local_holds_detected": True}), \
             patch("sys.stdout", new_callable=StringIO):
            run.return_value.returncode = 0
            self.assertEqual(module.main(), 3)

    def test_too_many_service_records_rejected_before_journal_traversal(self):
        inventory = self.inventory()
        inventory["loaded"] = inventory["loaded"] * 22
        with self.assertRaisesRegex(ValueError, "audit limit"):
            audit_inventory(inventory, self.home, verify=lambda _: None)

    def test_lock_inspection_failure_still_preserves_journal_report(self):
        result = audit_inventory(
            self.inventory(), self.home, verify=lambda _: {"finished": 0},
            inspect_locks=lambda _: (_ for _ in ()).throw(ValueError("unavailable")),
        )
        self.assertEqual(result["unique_journal_dirs_checked"], 2)
        self.assertFalse(result["lock_descriptor_evidence"]["exclusive_lock_proved"])
        self.assertEqual(result["lock_descriptor_evidence"]["reason"],
                         "lock descriptor inspection unavailable")


if __name__ == "__main__":
    unittest.main()
