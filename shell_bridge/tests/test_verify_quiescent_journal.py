import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge
from verify_quiescent_journal import verify, UnresolvedJournalError

CUTOVER = Path(__file__).resolve().parents[1] / "cutover.sh"
VERIFY = Path(__file__).resolve().parents[1] / "verify_quiescent_journal.py"


class QuiescentJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.journal = self.state / "requests"
        self.journal.mkdir(parents=True)

    def record(self, name: str, status: str, *, started: bool = True):
        d = self.journal / name
        d.mkdir()
        if started:
            (d / "started.json").write_text('{"request_sha256":"foo"}')
        (d / "finished.json").write_text(json.dumps(
            {"status": status, "request_sha256": "foo"}))
        return d

    def test_all_valid_finished_states_are_quiescent(self):
        self.record("success", "completed")
        self.record("rejected", "rejected", started=False)
        result = verify(self.state)
        self.assertEqual(result["finished"], 2)
        self.assertEqual(result["indeterminate"], 0)
        self.assertEqual(result["unverifiable_finished"], 0)
        self.assertEqual(result["started_without_finished"], 0)

    def test_finished_but_indeterminate_blocks_cutover(self):
        self.record("unresolved", "indeterminate")
        self.assertEqual(bridge._result_state_counts({"state_dir": str(self.state)})
                         ["started_without_finished"], 0)
        with self.assertRaisesRegex(UnresolvedJournalError, "indeterminate"):
            verify(self.state)
        result = subprocess.run([sys.executable, str(VERIFY), str(self.state)],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertIn("JOURNAL_HANDOVER_HOLD", result.stderr)

    def test_started_without_finished_is_not_quiescent(self):
        d = self.journal / "started"
        d.mkdir()
        (d / "started.json").write_text("{}")
        with self.assertRaisesRegex(UnresolvedJournalError, "unresolved"):
            verify(self.state)

    def test_corrupt_unknown_or_symlink_finished_blocks(self):
        for text in ("not json", '{"status":"unknown"}', "[]"):
            with self.subTest(text=text):
                d = self.journal / "corrupt"
                d.mkdir(exist_ok=True)
                (d / "finished.json").write_text(text)
                with self.assertRaisesRegex(UnresolvedJournalError, "unreadable"):
                    verify(self.state)
                (d / "finished.json").unlink()
        d = self.journal / "corrupt"
        (d / "finished.json").symlink_to(self.journal / "missing")
        with self.assertRaises(UnresolvedJournalError):
            verify(self.state)

    def test_symlinked_or_nonexistent_state_rejected(self):
        link = self.state.parent / "linked"
        link.symlink_to(self.state, target_is_directory=True)
        with self.assertRaises(UnresolvedJournalError):
            verify(link)
        with self.assertRaises(UnresolvedJournalError):
            verify(self.state.parent / "missing")

    def test_cutover_checks_journal_before_and_after_old_owner_shutdown(self):
        content = CUTOVER.read_text()
        gate = 'verify_quiescent_journal.py" "$LEGACY_STATE_DIR"'
        self.assertEqual(content.count(gate), 2)
        first = content.index(gate)
        stop = content.index('# Stop old consumers first.')
        second = content.rindex(gate)
        boot = content.index('launchctl bootstrap "gui/${UID_NOW}" "$NEW_PLIST"')
        self.assertLess(first, stop)
        self.assertLess(stop, second)
        self.assertLess(second, boot)


if __name__ == "__main__":
    unittest.main()
