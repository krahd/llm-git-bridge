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
        self.assertFalse(report["exclusive_lock_proved"])
        self.assertFalse(report["lock_descriptor_evidence"]["exclusive_lock_proved"])
        self.assertFalse(report["remote_mailbox_quiescence_verified"])
        self.assertFalse(report["retirement_authorized"])
        self.assertFalse(report["mutations_performed"])

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
        self.assertFalse(result["retirement_authorized"])

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
