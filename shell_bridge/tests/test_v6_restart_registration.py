import os
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v6_restart_registration import (
    RegistrationHold, archive, restore, publish, quarantine,
)


class V6RestartRegistrationTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.home = Path(td.name) / "home"
        self.agents = self.home / "Library" / "LaunchAgents"
        self.agents.mkdir(parents=True)
        self.source_label = "net.laurenzo.local-executor-bridge"
        self.new_label = "net.laurenzo.local-executor-bridge-v6-next"
        self.source = self.agents / (self.source_label + ".plist")
        self.archive_dir = self.agents / ("retired-v6-" + "a" * 12)
        self.staged = self.home / ".local" / "share" / "new" / "launchagent.plist"
        self.staged.parent.mkdir(parents=True)
        self.destination = self.agents / (self.new_label + ".plist")
        self._plist(self.source, self.source_label)
        self._plist(self.staged, self.new_label)

    def _plist(self, path, label):
        path.write_bytes(plistlib.dumps({
            "Label": label, "ProgramArguments": [
                "/usr/bin/python3", "/private/install/bridge.py",
                "daemon", "--config", "/private/config.json",
            ], "RunAtLoad": True, "KeepAlive": True,
        }))
        path.chmod(0o600)

    def test_safe_archive_then_persist_replacement_without_starting_processes(self):
        original = self.source.read_bytes()
        replacement = self.staged.read_bytes()
        archive(self.source, self.archive_dir, self.source_label)
        self.assertFalse(self.source.exists())
        self.assertEqual(
            (self.archive_dir / self.source.name).read_bytes(), original)
        publish(self.staged, self.destination, self.new_label)
        self.assertEqual(self.destination.read_bytes(), replacement)
        self.assertTrue(self.staged.exists())
        self.assertTrue((self.archive_dir / self.source.name).exists())
        with self.assertRaises(RegistrationHold):
            publish(self.staged, self.destination, self.new_label)

    def test_restore_only_when_original_registration_absent(self):
        archive(self.source, self.archive_dir, self.source_label)
        archived = self.archive_dir / self.source.name
        restore(archived, self.source, self.source_label)
        self.assertTrue(self.source.is_file())
        self.assertFalse(archived.exists())
        restore(archived, self.source, self.source_label)
        self.assertTrue(self.source.is_file())

    def test_ambiguous_candidate_is_quarantined_not_restarted(self):
        archive(self.source, self.archive_dir, self.source_label)
        publish(self.staged, self.destination, self.new_label)
        failed = self.archive_dir / ("failed-candidate-" + self.new_label + ".plist")
        quarantine(self.destination, failed, self.new_label)
        self.assertFalse(self.destination.exists())
        self.assertEqual(failed.read_bytes(), self.staged.read_bytes())
        quarantine(self.destination, failed, self.new_label)
        self.assertTrue((self.archive_dir / self.source.name).is_file())

    def test_symlink_and_untrusted_label_block(self):
        external = self.home / "other.plist"
        external.write_bytes(self.source.read_bytes())
        self.source.unlink()
        self.source.symlink_to(external)
        with self.assertRaises(RegistrationHold):
            archive(self.source, self.archive_dir, self.source_label)
        with self.assertRaises(RegistrationHold):
            publish(self.staged, self.destination, "../unsafe")

    def test_foreign_nonprivate_registration_rejected(self):
        self.source.chmod(0o666)
        with self.assertRaisesRegex(RegistrationHold, "private"):
            archive(self.source, self.archive_dir, self.source_label)

    def test_occupied_archive_is_not_overwritten(self):
        self.archive_dir.mkdir()
        with self.assertRaisesRegex(RegistrationHold, "stale"):
            archive(self.source, self.archive_dir, self.source_label)
        self.assertTrue(self.source.exists())

    def test_candidate_must_be_inert_before_persistence(self):
        with self.assertRaises(RegistrationHold):
            publish(self.source, self.destination, self.new_label)
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
