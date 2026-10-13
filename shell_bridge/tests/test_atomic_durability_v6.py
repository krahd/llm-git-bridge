"""Durability ordering for journal/admission writes (no service side effects)."""
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge


class AtomicJournalDurabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def traced_write(self, destination, data):
        events = []
        real_fsync = os.fsync
        real_replace = os.replace

        def observe_fsync(fd):
            kind = "directory_fsync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file_fsync"
            events.append(kind)
            return real_fsync(fd)

        def observe_replace(src, dest):
            events.append("replace")
            return real_replace(src, dest)

        with patch.object(bridge.os, "fsync", side_effect=observe_fsync), \
             patch.object(bridge.os, "replace", side_effect=observe_replace):
            bridge.atomic_write(destination, data)
        return events

    def test_file_flush_precedes_rename_and_directory_flush_follows(self):
        request_folder = self.root / "requests"
        request_folder.mkdir()
        path = request_folder / "started.json"
        events = self.traced_write(path, b'{"request_sha256":"test"}')
        self.assertEqual(events, ["file_fsync", "replace", "directory_fsync"])
        self.assertEqual(path.read_bytes(), b'{"request_sha256":"test"}')

    def test_new_request_and_operation_directories_are_synced_before_publish(self):
        for name in ("requests", "operations"):
            with self.subTest(name=name):
                path = self.root / name / "new-id" / "admitted.json"
                events = self.traced_write(path, b"durable")
                self.assertEqual(events,
                                 ["directory_fsync", "directory_fsync",
                                  "file_fsync", "replace", "directory_fsync"])
                self.assertEqual(path.read_bytes(), b"durable")

    def test_precreated_request_directory_parent_is_synced_before_admission(self):
        folder = self.root / "requests" / "original"
        folder.mkdir(parents=True)
        parent_inode = folder.parent.stat().st_ino
        observed = []
        real_fsync = os.fsync

        def inspect(fd):
            current = os.fstat(fd)
            observed.append(current.st_ino)
            return real_fsync(fd)

        with patch.object(bridge.os, "fsync", side_effect=inspect):
            bridge.ensure_directory_durable(folder)
        self.assertEqual(observed, [parent_inode])

    def test_precreated_directory_sync_failure_stops_admission(self):
        folder = self.root / "operations" / "op-hash"
        folder.mkdir(parents=True)
        with patch.object(bridge, "_sync_directory", side_effect=OSError("sync denied")):
            with self.assertRaisesRegex(OSError, "sync denied"):
                bridge.ensure_directory_durable(folder)

    def test_postrename_directory_sync_error_is_not_reported_as_success(self):
        folder = self.root / "requests"
        folder.mkdir()
        path = folder / "started.json"
        real_fsync = os.fsync

        def failure_after_publish(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("directory durability failed")
            return real_fsync(fd)

        with patch.object(bridge.os, "fsync", side_effect=failure_after_publish):
            with self.assertRaisesRegex(OSError, "directory durability failed"):
                bridge.atomic_write(path, b"started")
        # A failed post-rename durability check is an ambiguous write: it
        # already changed the visible path and must not be blindly replayed.
        self.assertEqual(path.read_bytes(), b"started")
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["started.json"])


if __name__ == "__main__":
    unittest.main()
