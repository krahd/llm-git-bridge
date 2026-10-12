import hashlib
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
        sha = hashlib.sha256(name.encode()).hexdigest()
        if started:
            (d / "started.json").write_text(json.dumps({"request_sha256": sha}))
        (d / "result.json").write_text(json.dumps(
            {"protocol": 1, "kind": "result", "id": name, "status": status,
             "request_sha256": sha}))
        (d / "finished.json").write_text(json.dumps(
            {"status": status, "request_sha256": sha}))
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
                with self.assertRaises(UnresolvedJournalError):
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

    def test_finished_marker_without_result_must_hold(self):
        folder = self.record("lost-result", "completed")
        (folder / "result.json").unlink()
        with self.assertRaisesRegex(UnresolvedJournalError, "no matching local result"):
            verify(self.state)

    def test_persisted_result_without_finished_must_hold(self):
        folder = self.record("lost-finished", "completed")
        (folder / "finished.json").unlink()
        with self.assertRaisesRegex(UnresolvedJournalError, "no durable FINISHED"):
            verify(self.state)

    def test_mismatched_status_sha_and_request_id_must_hold(self):
        folder = self.record("mismatch", "completed")
        original = json.loads((folder / "result.json").read_text())
        for changes in (
            {"status": "rejected"},
            {"request_sha256": hashlib.sha256(b"other").hexdigest()},
            {"id": "different-id"},
            {"protocol": 2},
            {"kind": "unknown"},
        ):
            with self.subTest(changes=changes):
                (folder / "result.json").write_text(json.dumps({**original, **changes}))
                with self.assertRaisesRegex(UnresolvedJournalError, "disagree"):
                    verify(self.state)
        (folder / "result.json").write_text(json.dumps(original))

    def test_stale_active_marker_or_symlinked_result_holds(self):
        folder = self.record("active", "completed")
        active = folder / "active.json"
        active.write_text(json.dumps({"pgid": 123, "request_id": "active"}))
        with self.assertRaisesRegex(UnresolvedJournalError, "active process evidence"):
            verify(self.state)
        active.unlink()
        result = folder / "result.json"
        result.unlink()
        result.symlink_to(self.journal / "outside")
        with self.assertRaisesRegex(UnresolvedJournalError, "symlinked"):
            verify(self.state)

    def test_too_large_result_fails_before_read(self):
        folder = self.record("oversize", "completed")
        path = folder / "result.json"
        with path.open("ab") as handle:
            handle.truncate(48 * 1024 * 1024 + 1)
        with self.assertRaisesRegex(UnresolvedJournalError, "bounded audit"):
            verify(self.state)

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
