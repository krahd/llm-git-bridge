import json
import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import inspect_indeterminate_journal as triage


class UnresolvedReceiptInventoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / "state"
        self.requests = self.state / "requests"
        self.requests.mkdir(parents=True)

    def receipt(self, name, *, start=True, finished="completed", result="completed"):
        folder = self.requests / name
        folder.mkdir()
        if start:
            (folder / "started.json").write_text('{"request_sha256":"placeholder"}')
        if finished is not None:
            (folder / "finished.json").write_text(json.dumps({"status": finished}))
        if result is not None:
            (folder / "result.json").write_text(json.dumps({
                "status": result, "command": "DO_NOT_ECHO_PRIVATE_COMMAND",
                "stdout": "DO_NOT_ECHO_PRIVATE_OUTPUT",
            }))
        return folder

    def test_completed_indeterminate_and_unfinished_are_distinguished(self):
        self.receipt("complete")
        self.receipt("indeterminate", finished="indeterminate", result="indeterminate")
        self.receipt("unmatched", finished="completed", result="indeterminate")
        self.receipt("unstarted", finished=None, result=None)
        report = triage.inspect(self.state)
        self.assertTrue(report["complete_scan"])
        self.assertEqual(report["scanned"], 4)
        self.assertEqual(report["issue_count"], 3)
        self.assertEqual(report["indeterminate_count"], 2)
        self.assertEqual(report["started_without_finished_count"], 1)
        self.assertFalse(report["handover_authorized"])
        self.assertFalse(report["mutations_performed"])
        body = json.dumps(report)
        self.assertNotIn("DO_NOT_ECHO_PRIVATE_COMMAND", body)
        self.assertNotIn("DO_NOT_ECHO_PRIVATE_OUTPUT", body)
        self.assertIn("indeterminate", body)
        by_id = {r["request_id"]: r for r in report["examples"]}
        self.assertIn("receipt_pair_mismatch", by_id["unmatched"]["labels"])

    def test_invalid_and_linked_markers_are_flagged_without_following(self):
        folder = self.receipt("linked")
        (folder / "result.json").unlink()
        (folder / "result.json").symlink_to(self.state.parent / "secret")
        report = triage.inspect(self.state)
        self.assertEqual(report["unverifiable_count"], 1)
        self.assertIn("linked_marker", report["examples"][0]["labels"])

    def test_large_receipt_is_not_read_to_bypass_budget(self):
        folder = self.receipt("giant")
        with (folder / "result.json").open("ab") as handle:
            handle.truncate(1024 * 1024 + 1)
        report = triage.inspect(self.state)
        self.assertEqual(report["unverifiable_count"], 1)
        self.assertEqual(report["examples"][0]["result_status"], "unverifiable")

    def test_bounded_scan_fails_instead_of_reporting_complete(self):
        self.receipt("one")
        self.receipt("two")
        with patch.object(triage, "_MAX_REQUEST_DIRS", 1):
            with self.assertRaisesRegex(triage.InventoryUnavailable, "bounded scan"):
                triage.inspect(self.state)

    def test_symlinked_state_is_rejected(self):
        linked = self.state.parent / "alias"
        linked.symlink_to(self.state, target_is_directory=True)
        with self.assertRaises(triage.InventoryUnavailable):
            triage.inspect(linked)

    def test_examples_are_bounded_but_issue_count_is_not(self):
        for i in range(5):
            self.receipt("bad-%d" % i, finished="indeterminate", result="indeterminate")
        with patch.object(triage, "_MAX_EXAMPLES", 2):
            report = triage.inspect(self.state)
        self.assertEqual(report["issue_count"], 5)
        self.assertEqual(report["indeterminate_count"], 5)
        self.assertEqual(len(report["examples"]), 2)
        self.assertTrue(report["examples_truncated"])


if __name__ == "__main__":
    unittest.main()
