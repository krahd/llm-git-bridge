"""Regressions for the observed duplicate-execution defects (issue #39).

On the production Mac a v5 daemon locked ``instance:<bridge_instance_id>``
while a v6 daemon on the SAME mailbox locked ``drive-mailbox:<root>``. Both
held "their" lock and both consumed requests. Separately, STARTED was created
with a replacing write after a non-atomic existence check.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge as b

BRIDGE_DIR = str(Path(__file__).resolve().parents[1])


def legacy_lock_path(tmpdir: Path, identity: str) -> Path:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return tmpdir / f"chatgpt-shell-bridge-{os.getuid()}" / f"{digest}.lock"


def hold_legacy_lock(path: Path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle = open(path, "a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    return handle


class HermeticLockTestCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        self.tmpdir = self.td / "launchd-TMPDIR"
        self.durable = self.td / "durable-locks"
        patches = [
            patch.object(b.tempfile, "gettempdir", return_value=str(self.tmpdir)),
            patch.object(b, "_durable_lock_root", return_value=self.durable),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._td.cleanup)
        # Unique per test so the shared fixed /tmp directory never collides.
        self.suffix = self.td.name

    def production_cfg(self, **extra):
        cfg = {"remote": "drive:", "base_path": "Bridge " + self.suffix,
               "drive_root_folder_id": "root-" + self.suffix,
               "bridge_instance_id": "instance-" + self.suffix}
        cfg.update(extra)
        return cfg


class MailboxLockIdentityUnionTests(HermeticLockTestCase):
    def test_running_legacy_instance_lock_blocks_pinned_root_v6(self):
        cfg = self.production_cfg()
        legacy = hold_legacy_lock(legacy_lock_path(
            self.tmpdir, "instance:" + cfg["bridge_instance_id"]))
        try:
            with self.assertRaisesRegex(RuntimeError, "already holds the instance lock"):
                b.acquire_instance_lock(cfg)
        finally:
            legacy.close()
        b.acquire_instance_lock(cfg).close()

    def test_running_pinned_root_v6_lock_blocks_instance_scoped_config(self):
        cfg = self.production_cfg()
        older_v6 = hold_legacy_lock(legacy_lock_path(
            self.tmpdir, "drive-mailbox:" + cfg["drive_root_folder_id"]))
        try:
            # Same mailbox, but a different journal and instance marker copy.
            with self.assertRaisesRegex(RuntimeError, "already holds the instance lock"):
                b.acquire_instance_lock(self.production_cfg(
                    bridge_instance_id="other-" + self.suffix, state_dir="/elsewhere"))
        finally:
            older_v6.close()

    def test_new_owner_blocks_both_historical_lock_schemes(self):
        cfg = self.production_cfg()
        owner = b.acquire_instance_lock(cfg)
        try:
            for identity in ("instance:" + cfg["bridge_instance_id"],
                             "drive-mailbox:" + cfg["drive_root_folder_id"],
                             f"mailbox:{cfg['remote']}|{cfg['base_path']}"):
                with self.subTest(identity=identity):
                    path = legacy_lock_path(self.tmpdir, identity)
                    with open(path, "a+") as handle, self.assertRaises(BlockingIOError):
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            owner.close()

    def test_unrelated_mailbox_and_instance_do_not_contend(self):
        first = b.acquire_instance_lock(self.production_cfg())
        try:
            b.acquire_instance_lock({
                "remote": "drive:", "base_path": "Other " + self.suffix,
                "drive_root_folder_id": "other-root-" + self.suffix,
                "bridge_instance_id": "other-instance-" + self.suffix}).close()
        finally:
            first.close()

    def test_durable_lock_is_held_outside_temporary_directories(self):
        owner = b.acquire_instance_lock(self.production_cfg())
        try:
            held = {str(path.parent) for path, _, _ in owner.held}
            self.assertIn(str(self.durable), held)
        finally:
            owner.close()


class LockIntegrityTests(HermeticLockTestCase):
    def test_cleaned_lock_file_is_reasserted_and_still_excludes_claimants(self):
        cfg = self.production_cfg()
        owner = b.acquire_instance_lock(cfg)
        try:
            # Simulate periodic temporary-directory cleanup.
            for path, _, _ in owner.held:
                path.unlink()
            self.assertTrue(owner.verify())
            for path, _, _ in owner.held:
                self.assertTrue(path.exists())
            with self.assertRaisesRegex(RuntimeError, "already holds the instance lock"):
                b.acquire_instance_lock(cfg)
        finally:
            owner.close()

    def test_lost_lock_inode_taken_by_another_claimant_is_detected(self):
        cfg = self.production_cfg()
        owner = b.acquire_instance_lock(cfg)
        try:
            victim = owner.held[0][0]
            victim.unlink()
            intruder = hold_legacy_lock(victim)
            try:
                self.assertFalse(owner.verify())
            finally:
                intruder.close()
        finally:
            owner.close()

    def test_daemon_stops_admission_on_lock_loss_without_aborting_work(self):
        # daemon() leaves the module-wide shutdown event set; later tests in
        # this process would otherwise see every run_shell abort its child.
        self.addCleanup(b._SHUTDOWN.clear)
        fake_lock = MagicMock()
        fake_lock.verify.return_value = False
        lister = MagicMock(return_value=[])
        with patch.object(b, "load_config", return_value={"poll_seconds": 0.01}), \
             patch.object(b, "acquire_instance_lock", return_value=fake_lock), \
             patch.object(b, "WakeLease"), \
             patch.object(b, "ensure_remote_dirs"), \
             patch.object(b, "_result_state_counts", return_value={}), \
             patch.object(b, "sweep_incomplete_processes", return_value=0), \
             patch.object(b, "max_active_requests", return_value=2), \
             patch.object(b, "list_requests_cfg", lister), \
             patch.object(b, "_terminate_pgid") as terminate:
            with b._ACTIVE_LOCK:
                b._ACTIVE["live-request"] = {"pgid": 424242, "started_at": 0, "name": "x"}
            try:
                with self.assertRaises(SystemExit) as raised:
                    b.daemon(Path("/nonexistent/config.json"))
            finally:
                with b._ACTIVE_LOCK:
                    b._ACTIVE.pop("live-request", None)
        self.assertEqual(raised.exception.code, 75)
        lister.assert_not_called()
        terminate.assert_not_called()
        fake_lock.close.assert_called_once()


class RequestFixture:
    def __init__(self, td: Path, rid: str = "dup-check-001"):
        self.root = td / "root"
        self.root.mkdir()
        self.state = td / "state"
        self.rid = rid
        self.raw = json.dumps({
            "protocol": 1, "id": rid, "cwd": str(self.root), "command": "true",
            "explanation": "Check exactly-once execution.",
        }).encode()
        self.cfg = {"remote": "x:", "base_path": "Bridge", "state_dir": str(self.state),
                    "allowed_root": str(self.root), "shell": b.DEFAULT_SHELL,
                    "operator_confirmation_mode": "reject"}
        self.uploaded = []

    def patches(self, run_shell):
        raw = self.raw

        def from_remote(remote, base, leaf, local):
            Path(local).write_bytes(raw)

        def to_remote(local, remote, base, leaf):
            self.uploaded.append((leaf, Path(local).read_bytes()))
        return [patch.object(b, "copy_from_remote", from_remote),
                patch.object(b, "copy_to_remote", to_remote),
                patch.object(b, "delete_remote", lambda *a: None),
                patch.object(b, "run_shell", run_shell)]


class ExactlyOnceRequestTests(unittest.TestCase):
    def test_concurrent_workers_execute_one_request_once(self):
        with tempfile.TemporaryDirectory() as td:
            fx = RequestFixture(Path(td))
            calls = []
            entered = threading.Event()
            release = threading.Event()

            def slow_shell(*args, **kwargs):
                calls.append(1)
                entered.set()
                release.wait(5)
                return {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False}
            patches = fx.patches(slow_shell)
            for p in patches:
                p.start()
            try:
                first = threading.Thread(target=b.process_one, args=(fx.rid + ".json", fx.cfg))
                first.start()
                self.assertTrue(entered.wait(5))
                # A duplicate consumer arrives while the original is running.
                b.process_one(fx.rid + ".json", fx.cfg)
                self.assertEqual(fx.uploaded, [], "duplicate must not publish")
                release.set()
                first.join(5)
                # A later replay of the same transport ID republishes only.
                b.process_one(fx.rid + ".json", fx.cfg)
            finally:
                release.set()
                for p in reversed(patches):
                    p.stop()
            self.assertEqual(len(calls), 1)
            results = [json.loads(body) for leaf, body in fx.uploaded]
            self.assertEqual([r["status"] for r in results], ["completed", "completed"])
            self.assertEqual(results[0], results[1])

    def test_separate_processes_sharing_journal_execute_once(self):
        with tempfile.TemporaryDirectory() as td:
            fx = RequestFixture(Path(td), "dup-proc-001")
            counter = Path(td) / "executions"
            script = r"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import bridge as b
cfg = json.loads(sys.argv[2]); raw = sys.argv[3].encode(); counter = Path(sys.argv[4])
b.copy_from_remote = lambda remote, base, leaf, local: Path(local).write_bytes(raw)
b.copy_to_remote = lambda *a: None
b.delete_remote = lambda *a: None
def shell(*a, **k):
    with counter.open("a") as f: f.write("x\n")
    time.sleep(1.5)
    return {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False}
b.run_shell = shell
b.process_one(sys.argv[5], cfg)
"""
            argv = [sys.executable, "-c", script, BRIDGE_DIR, json.dumps(fx.cfg),
                    fx.raw.decode(), str(counter), fx.rid + ".json"]
            procs = [subprocess.Popen(argv, stderr=subprocess.PIPE) for _ in range(2)]
            for proc in procs:
                _, err = proc.communicate(timeout=30)
                self.assertEqual(proc.returncode, 0, err)
            self.assertEqual(counter.read_text().count("x"), 1)

    def test_started_marker_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "started.json"
            b.atomic_create(marker, b'{"request_sha256":"first"}')
            with self.assertRaises(FileExistsError):
                b.atomic_create(marker, b'{"request_sha256":"second"}')
            self.assertEqual(marker.read_bytes(), b'{"request_sha256":"first"}')
            self.assertEqual(sorted(p.name for p in Path(td).iterdir()), ["started.json"])

    def test_execution_refused_if_started_appears_after_admission_checks(self):
        with tempfile.TemporaryDirectory() as td:
            fx = RequestFixture(Path(td), "late-started-001")
            ran = []
            real_create = b.atomic_create

            def racing_create(path, data):
                if path.name == "started.json":
                    real_create(path, b'{"request_sha256":"elsewhere"}')
                return real_create(path, data)
            patches = fx.patches(lambda *a, **k: ran.append(1))
            patches.append(patch.object(b, "atomic_create", racing_create))
            for p in patches:
                p.start()
            try:
                b.process_one(fx.rid + ".json", fx.cfg)
            finally:
                for p in reversed(patches):
                    p.stop()
            self.assertEqual(ran, [])
            self.assertEqual(fx.uploaded, [])


class ProcessGroupReuseTests(unittest.TestCase):
    def test_process_group_recorded_before_boot_is_never_signalled(self):
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "active.json"
            marker.write_text(json.dumps({"request_id": "old", "pgid": 4321,
                                          "spawned_at": 1000.0}))
            with patch.object(b, "_boot_time", return_value=2000.0), \
                 patch.object(b.os, "killpg") as killpg, \
                 patch.object(b, "_terminate_pgid") as terminate:
                b._contain_recorded_active(marker, "old")
            killpg.assert_not_called()
            terminate.assert_not_called()

    def test_process_group_from_current_boot_is_contained(self):
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "active.json"
            marker.write_text(json.dumps({"request_id": "live", "pgid": 4321,
                                          "spawned_at": 3000.0}))
            with patch.object(b, "_boot_time", return_value=2000.0), \
                 patch.object(b.os, "killpg"), \
                 patch.object(b, "_terminate_pgid") as terminate:
                b._contain_recorded_active(marker, "live")
            terminate.assert_called_once_with(4321)

    def test_boot_time_is_available_on_supported_hosts(self):
        boot = b._boot_time()
        self.assertIsNotNone(boot)
        self.assertLess(boot, time.time())


if __name__ == "__main__":
    unittest.main()
