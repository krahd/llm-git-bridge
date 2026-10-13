import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from verify_pending_approvals import ApprovalQueueHold, verify


class ApprovalQueueDrainTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = pathlib.Path(tmp.name) / "production"
        self.state.mkdir()

    def test_missing_optional_approval_queue_is_empty(self):
        verify(self.state)

    def test_empty_approval_queue_is_clean(self):
        (self.state / "approvals" / "pending").mkdir(parents=True)
        verify(self.state)

    def test_pending_approval_never_silently_ignored(self):
        pending = self.state / "approvals" / "pending"
        pending.mkdir(parents=True)
        (pending / "old-expired.json").write_text('{"expires_at":0}')
        with self.assertRaisesRegex(ApprovalQueueHold, "not empty"):
            verify(self.state)

    def test_unknown_marker_is_still_a_hold(self):
        pending = self.state / "approvals" / "pending"
        pending.mkdir(parents=True)
        (pending / "invalid-marker").write_text("unknown")
        with self.assertRaises(ApprovalQueueHold):
            verify(self.state)

    def test_symlinked_approvals_or_pending_is_rejected(self):
        outside = self.state.parent / "outside"
        outside.mkdir()
        (self.state / "approvals").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ApprovalQueueHold):
            verify(self.state)
        (self.state / "approvals").unlink()
        (self.state / "approvals").mkdir()
        (self.state / "approvals" / "pending").symlink_to(
            outside, target_is_directory=True)
        with self.assertRaises(ApprovalQueueHold):
            verify(self.state)

    def test_broken_approval_link_is_not_absence(self):
        (self.state / "approvals").symlink_to(self.state / "missing", target_is_directory=True)
        with self.assertRaises(ApprovalQueueHold):
            verify(self.state)

    def test_missing_or_relative_state_is_not_quiescent(self):
        with self.assertRaises(ApprovalQueueHold):
            verify(self.state / "missing")
        with self.assertRaises(ApprovalQueueHold):
            verify(pathlib.Path("relative"))

    def test_controller_checks_before_after_and_rollback(self):
        c = (ROOT / "cutover.sh").read_text()
        marker = "verify_pending_approvals.py"
        self.assertEqual(c.count(marker), 3)
        stop = c.index("# Stop old consumers first.")
        before = c.index(marker)
        after = c.index(marker, stop)
        start = c.index('launchctl bootstrap "gui/$' + '{UID_NOW}" "$NEW_PLIST"')
        self.assertLess(before, stop)
        self.assertLess(stop, after)
        self.assertLess(after, start)
        rollback = c.split("rollback() {", 1)[1].split("trap rollback EXIT", 1)[0]
        self.assertIn(marker, rollback)
        self.assertIn("V6_UNSAFE_TO_RESTORE=1", rollback)


if __name__ == "__main__":
    unittest.main()
