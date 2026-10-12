import json
import fcntl
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

import bridge

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge_lock_observer as locks


class LockDescriptorEvidenceTests(unittest.TestCase):
    def test_lock_path_matches_mailbox_scoped_algorithm(self):
        one = locks.lock_path("production", uid=501, tmp=Path("/tmp"))
        two = locks.lock_path("production", uid=501, tmp=Path("/tmp"))
        other = locks.lock_path("staging", uid=501, tmp=Path("/tmp"))
        self.assertEqual(one, two)
        self.assertNotEqual(one, other)
        self.assertEqual(one.parent.name, "chatgpt-shell-bridge-501")
        self.assertEqual(len(one.stem), 32)
        for invalid in ("", "../escape", "name with spaces", "a" * 129):
            with self.subTest(value=invalid):
                with self.assertRaises(ValueError):
                    locks.lock_path(invalid)

    def test_lock_path_tracks_the_actual_daemon_lock_implementation(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(bridge.tempfile, "gettempdir", return_value=td):
                expected = bridge._instance_lock_path(
                    {"drive_root_folder_id": "production"})
                observed = locks.lock_path("production")
                self.assertEqual(expected, observed)

    @unittest.skipUnless(sys.platform == "darwin", "Mac lsof integration")
    def test_real_macos_lsof_sees_current_exclusive_lock_holder(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bridge-test.lock"
            with path.open("a+") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                observed = locks.open_lock_file_pids(path)
                self.assertEqual(observed["state"], "open_descriptors_observed", observed)
                self.assertIn(str(os.getpid()), observed["pids"])
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def test_absent_file_never_invokes_lsof(self):
        with tempfile.TemporaryDirectory() as td:
            calls = []
            value = locks.open_lock_file_pids(
                Path(td) / "absent.lock",
                runner=lambda *a, **k: calls.append(a))
            self.assertEqual(value["state"], "not_present")
            self.assertEqual(calls, [])

    def test_open_file_pids_are_evidence_not_exclusive_owner(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "lock"
            path.write_text("")
            calls = []
            def runner(argv, **kw):
                calls.append((argv, kw))
                return types.SimpleNamespace(returncode=0, stdout="1786\n1738\n", stderr="")
            data = locks.open_lock_file_pids(path, runner=runner)
            self.assertEqual(data, {"state": "open_descriptors_observed", "pids": ["1738", "1786"]})
            self.assertEqual(calls[0][0], ["/usr/sbin/lsof", "-w", "-t", str(path)])
            self.assertEqual(path.read_text(), "")

    def test_ambiguous_lsof_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "lock"
            path.touch()
            for result in (
                types.SimpleNamespace(returncode=1, stdout="", stderr=""),
                types.SimpleNamespace(returncode=0, stdout="nsecret\n", stderr=""),
                types.SimpleNamespace(returncode=0, stdout="1786\n", stderr="warning"),
            ):
                with self.subTest(result=result):
                    evidence = locks.open_lock_file_pids(
                        path, runner=lambda *args, **kwargs: result)
                    self.assertEqual(evidence["state"], "unverified")
                    self.assertEqual(evidence["pids"], [])

    def test_symlink_lock_is_rejected_without_open(self):
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / "real"
            a.touch()
            b = Path(td) / "link"
            b.symlink_to(a)
            data = locks.open_lock_file_pids(
                b, runner=lambda *args, **kwargs: self.fail("lsof must not run"))
            self.assertEqual(data["state"], "unverified")

    def test_retirement_is_never_inferred_from_open_file_evidence(self):
        inventory = {
            "kind": "loaded_bridge_ownership", "safe_to_stage": False,
            "loaded": [
                {"label": "one", "pid": "1786", "drive_root_folder_id": "prod"},
                {"label": "two", "pid": "1738", "drive_root_folder_id": "prod"},
            ],
        }
        data = locks.inspect(inventory, runner=lambda *a, **k: self.fail("no lock file"))
        self.assertFalse(data["exclusive_lock_proved"])
        self.assertFalse(data["retirement_authorized"])
        self.assertFalse(data["source_safe_to_stage"])
        self.assertFalse(data["mutations_performed"])
        self.assertEqual(data["lock_evidence"][0]["claimant_pids"], ["1738", "1786"])


if __name__ == "__main__":
    unittest.main()
