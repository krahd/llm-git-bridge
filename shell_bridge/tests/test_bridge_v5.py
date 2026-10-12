import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge as b


class V5ConfigTests(unittest.TestCase):
    def test_optional_utf8_bom_request_is_accepted_without_changing_original_hash(self):
        request = {"protocol": 1, "id": "bom-safe", "command": "true"}
        canonical = json.dumps(request).encode("utf-8")
        prefixed = bytes.fromhex("efbbbf") + canonical
        self.assertEqual(b.load_json_bytes(prefixed), request)
        self.assertEqual(b.load_json_bytes(canonical), request)
        self.assertNotEqual(b.sha256_bytes(prefixed), b.sha256_bytes(canonical))
        # A second BOM and non-UTF-8 encodings must still fail closed.
        with self.assertRaises((ValueError, UnicodeError)):
            b.load_json_bytes(bytes.fromhex("efbbbfefbbbf") + canonical)
        with self.assertRaises((ValueError, UnicodeError)):
            b.load_json_bytes(canonical.decode("utf-8").encode("utf-16"))
        with self.assertRaises(ValueError):
            b.load_json_bytes(bytes.fromhex("efbbbf") + b"[1,2,3]")

    def test_root_id_allows_missing_base_path_and_targets_relative_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            cfgp = Path(td) / "cfg.json"
            cfgp.write_text(json.dumps({
                "remote": "x:", "drive_root_folder_id": "root123",
                "allowed_root": str(root), "state_dir": str(Path(td)/"state"),
                "shell": b.DEFAULT_SHELL,
            }))
            cfg = b.load_config(cfgp)
            self.assertEqual(b._target(cfg, "requests"), "x:requests")
            self.assertEqual(b._rclone_prefix(cfg), ["--drive-root-folder-id", "root123"])

    def test_auto_limit_is_bounded_and_integer_override_validated(self):
        auto = b.max_active_requests({})
        self.assertGreaterEqual(auto, 4)
        self.assertLessEqual(auto, b.DEFAULT_MAX_ACTIVE_CAP)
        self.assertEqual(b.max_active_requests({"max_active_requests": 9}), 9)
        for bad in [0, -1, True, "9", 257]:
            with self.assertRaises(ValueError):
                b.max_active_requests({"max_active_requests": bad})


class V5InstanceLockTests(unittest.TestCase):
    def test_same_instance_id_cannot_lock_different_state_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            cfg1={"state_dir":str(Path(td)/"state-a"),"bridge_instance_id":"same-instance","drive_root_folder_id":"root123"}
            cfg2={"state_dir":str(Path(td)/"state-b"),"bridge_instance_id":"same-instance","drive_root_folder_id":"root123"}
            first=b.acquire_instance_lock(cfg1)
            try:
                with self.assertRaisesRegex(RuntimeError, "another shell-bridge instance"):
                    b.acquire_instance_lock(cfg2)
            finally:
                first.close()
            second=b.acquire_instance_lock(cfg2)
            second.close()

    def test_distinct_instance_ids_cannot_consume_same_mailbox(self):
        with tempfile.TemporaryDirectory() as td:
            cfg1={"state_dir":str(Path(td)/"state-a"),"bridge_instance_id":"instance-a","drive_root_folder_id":"root123"}
            cfg2={"state_dir":str(Path(td)/"state-b"),"bridge_instance_id":"instance-b","drive_root_folder_id":"root123"}
            first=b.acquire_instance_lock(cfg1)
            try:
                with self.assertRaisesRegex(RuntimeError,"another shell-bridge instance"):
                    b.acquire_instance_lock(cfg2)
            finally:
                first.close()
            cfg2["drive_root_folder_id"]="different-mailbox"
            second=b.acquire_instance_lock(cfg2)
            second.close()


class MailboxLockMigrationTests(unittest.TestCase):
    def test_different_tmpdirs_share_one_fixed_mailbox_lock(self):
        with tempfile.TemporaryDirectory() as td:
            one = Path(td) / "a"
            two = Path(td) / "b"
            cfg = {"drive_root_folder_id": "migration-" + Path(td).name}
            with patch.object(b.tempfile, "gettempdir", return_value=str(one)):
                first = b.acquire_instance_lock(cfg)
            try:
                with patch.object(b.tempfile, "gettempdir", return_value=str(two)):
                    with self.assertRaisesRegex(RuntimeError, "another shell-bridge instance"):
                        b.acquire_instance_lock(cfg)
            finally:
                first.close()
            with patch.object(b.tempfile, "gettempdir", return_value=str(two)):
                third = b.acquire_instance_lock(cfg)
                third.close()

    def test_cross_process_distinct_tmpdirs_cannot_both_own_one_mailbox(self):
        with tempfile.TemporaryDirectory() as td:
            one = Path(td) / "first"
            two = Path(td) / "second"
            cfg = {"drive_root_folder_id": "cross-" + Path(td).name}
            script = (
                "import sys; sys.path.insert(0,sys.argv[1]); import bridge; "
                "bridge.tempfile.gettempdir=lambda:sys.argv[2]; "
                "cfg={'drive_root_folder_id':sys.argv[3]}; "
                "bridge.acquire_instance_lock(cfg)"
            )
            args = [sys.executable, "-c", script,
                    str(Path(__file__).resolve().parents[1]), str(two),
                    cfg["drive_root_folder_id"]]
            with patch.object(b.tempfile, "gettempdir", return_value=str(one)):
                first = b.acquire_instance_lock(cfg)
            try:
                refused = b.subprocess.run(args, capture_output=True, text=True,
                                           timeout=8)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn("already holds the instance lock", refused.stderr)
            finally:
                first.close()
            accepted = b.subprocess.run(args, capture_output=True, text=True,
                                        timeout=8)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)

    def test_legacy_named_mailbox_and_pinned_id_share_stable_lock(self):
        with tempfile.TemporaryDirectory() as td:
            named = {"remote": "chatgpt-git-bridge:",
                     "base_path": "Local Executor Bridge"}
            canonical = {**named, "drive_root_folder_id": "production-root"}
            with patch.object(b.tempfile, "gettempdir",
                              return_value=str(Path(td) / "one")):
                first = b.acquire_instance_lock(canonical)
            try:
                with patch.object(b.tempfile, "gettempdir",
                                  return_value=str(Path(td) / "two")):
                    with self.assertRaisesRegex(RuntimeError, "another shell-bridge instance"):
                        b.acquire_instance_lock(named)
            finally:
                first.close()
            with patch.object(b.tempfile, "gettempdir",
                              return_value=str(Path(td) / "two")):
                second = b.acquire_instance_lock(named)
                second.close()

    def test_config_rejects_noncanonical_drive_root_identifier(self):
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / "config.json"
            base = {"remote": "chatgpt-git-bridge:", "base_path": "mailbox",
                    "allowed_root": td, "state_dir": td, "shell": sys.executable}
            for value in (True, 5, {}, "a/b", "with space", "X" * 129):
                with self.subTest(value=value):
                    config.write_text(json.dumps({**base, "drive_root_folder_id": value}))
                    with self.assertRaises(ValueError):
                        b.load_config(config)

    def test_fixed_lock_is_invariant_to_environment_tmpdir(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = {"drive_root_folder_id": "stable-mailbox-123"}
            with patch.object(b.tempfile, "gettempdir", return_value=str(Path(td) / "one")):
                a = b._stable_instance_lock_path(cfg)
                legacy_a = b._instance_lock_path(cfg)
            with patch.object(b.tempfile, "gettempdir", return_value=str(Path(td) / "two")):
                self.assertEqual(a, b._stable_instance_lock_path(cfg))
                self.assertNotEqual(legacy_a, b._instance_lock_path(cfg))

    def test_legacy_lock_path_derivation_does_not_create_directories(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "nonexistent-TMPDIR"
            with patch.object(b.tempfile, "gettempdir", return_value=str(root)):
                candidate = b._instance_lock_path({"drive_root_folder_id": "mailbox"})
                self.assertFalse(candidate.parent.exists())

    def test_lock_file_symlink_and_untrusted_directory_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "private"
            folder.mkdir(mode=0o700)
            target = Path(td) / "untouched"
            target.write_text("preserve")
            symlink = folder / "lock"
            symlink.symlink_to(target)
            with self.assertRaises(OSError):
                b._open_instance_lock_file(symlink)
            self.assertEqual(target.read_text(), "preserve")
            symlink.unlink()
            folder.chmod(0o755)
            with self.assertRaisesRegex(RuntimeError, "not exclusively owned"):
                b._open_instance_lock_file(folder / "another.lock")


class V5RcloneTests(unittest.TestCase):
    def test_rclone_timeout_is_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td) / "rclone"
            fake.write_text("#!/bin/sh\nsleep 5\n")
            fake.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{td}{os.pathsep}{env['PATH']}"
            with patch.dict(os.environ, env, clear=True):
                started = time.monotonic()
                with self.assertRaisesRegex(RuntimeError, "timed out"):
                    b.run_rclone(["version"], {"rclone_timeout_seconds": 1})
                self.assertLess(time.monotonic() - started, 3)

    def test_process_group_probe_permission_error_is_contained(self):
        calls = []
        def fake_killpg(pgid, sig):
            calls.append((pgid, sig))
            if sig == 0:
                raise PermissionError(1, "Operation not permitted")
        with patch.object(b.os, "killpg", fake_killpg):
            b._terminate_pgid(12345)
        self.assertIn((12345, b.signal.SIGTERM), calls)
        self.assertIn((12345, b.signal.SIGKILL), calls)

    def test_root_id_prefix_is_passed_to_rclone(self):
        class CP:
            returncode = 0
            stdout = b""
            stderr = b""
        seen = []
        def fake_run(argv, **kwargs):
            seen.append(argv)
            return CP()
        with patch.object(b.subprocess, "run", fake_run):
            b.run_rclone(["lsf", "x:requests"], {"drive_root_folder_id":"abc","rclone_timeout_seconds":5})
        self.assertEqual(seen[0][:4], ["rclone", "--drive-root-folder-id", "abc", "lsf"])

    def test_subprocess_fallback_calls_can_overlap(self):
        class CP:
            returncode = 0
            stdout = b""
            stderr = b""
        entered_first = threading.Event()
        entered_second = threading.Event()
        release = threading.Event()
        counter = {"n": 0}
        counter_lock = threading.Lock()
        errors = []
        def fake_run(argv, **kwargs):
            with counter_lock:
                counter["n"] += 1
                n = counter["n"]
                if n == 1:
                    entered_first.set()
                elif n == 2:
                    entered_second.set()
            release.wait(2)
            return CP()
        def call():
            try:
                b.run_rclone(["version"], {"rclone_timeout_seconds": 5})
            except Exception as exc:
                errors.append(exc)
        with patch.object(b.subprocess, "run", fake_run):
            t1 = threading.Thread(target=call); t1.start()
            self.assertTrue(entered_first.wait(0.5))
            t2 = threading.Thread(target=call); t2.start()
            try:
                self.assertTrue(entered_second.wait(0.5), "second fallback was globally serialized")
            finally:
                release.set(); t1.join(2); t2.join(2)
        self.assertEqual(errors, [])


class V5CrashTests(unittest.TestCase):
    def test_started_active_is_contained_before_indeterminate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)/"root"; root.mkdir()
            state = Path(td)/"state"
            rdir = state/"requests"/"jobx"; rdir.mkdir(parents=True)
            raw = json.dumps({"protocol":1,"id":"jobx","cwd":str(root),"explanation":"Test request.","command":"true"}).encode()
            (rdir/"request.json").write_bytes(raw)
            (rdir/"started.json").write_text(json.dumps({"request_sha256":b.sha256_bytes(raw)}))
            (rdir/"active.json").write_text(json.dumps({"request_id":"jobx","pgid":999999}))
            cfg={"remote":"x:","base_path":"Bridge","state_dir":str(state),"allowed_root":str(root),"shell":b.DEFAULT_SHELL}
            uploaded={}
            def from_remote(remote, base, leaf, local): Path(local).write_bytes(raw)
            def to_remote(local, remote, base, leaf): uploaded[leaf]=Path(local).read_bytes()
            with patch.object(b,"copy_from_remote",from_remote), patch.object(b,"copy_to_remote",to_remote), \
                 patch.object(b,"delete_remote",lambda *a:None), patch.object(b,"_terminate_pgid") as term, \
                 patch.object(b.os,"killpg",side_effect=ProcessLookupError):
                b.process_one("jobx.json",cfg)
            result=json.loads(uploaded["results/jobx.json"])
            self.assertEqual(result["status"],"indeterminate")
            self.assertFalse((rdir/"active.json").exists())
            term.assert_not_called()

    def test_recorded_active_permission_probe_still_attempts_containment(self):
        with tempfile.TemporaryDirectory() as td:
            marker=Path(td)/"active.json"
            marker.write_text(json.dumps({"request_id":"jobx","pgid":12345}))
            with patch.object(b.os,"killpg",side_effect=PermissionError(1,"Operation not permitted")), patch.object(b,"_terminate_pgid") as term:
                b._contain_recorded_active(marker,"jobx")
            term.assert_called_once_with(12345)

    def test_startup_sweep_contains_recorded_active_without_remote_request(self):
        with tempfile.TemporaryDirectory() as td:
            state=Path(td)/"state"; rdir=state/"requests"/"orphan"; rdir.mkdir(parents=True)
            (rdir/"started.json").write_text(json.dumps({"request_sha256":"x"}))
            (rdir/"active.json").write_text(json.dumps({"request_id":"orphan","pgid":12345}))
            with patch.object(b,"_contain_recorded_active") as contain:
                n=b.sweep_incomplete_processes({"state_dir":str(state)})
            self.assertEqual(n,1); contain.assert_called_once(); self.assertFalse((rdir/"active.json").exists())


class V5HealthTests(unittest.TestCase):
    def test_doctor_reports_private_oauth_without_exposing_client_id(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"root"; root.mkdir(); state=Path(td)/"state"
            cfg={"remote":"x:","base_path":"Bridge","drive_root_folder_id":"abc","allowed_root":str(root),"state_dir":str(state),"shell":b.DEFAULT_SHELL,"rclone_timeout_seconds":30}
            class CP:
                def __init__(self, returncode=0, stdout=b"", stderr=b""):
                    self.returncode=returncode; self.stdout=stdout; self.stderr=stderr
            def fake_run(args, _cfg, check=False, capture=True):
                if args == ["version"]: return CP(stdout=b"rclone v1.75.1\n")
                if args and args[0] == "about": return CP()
                if args[:2] == ["config", "redacted"]:
                    return CP(stdout=b"[x]\ntype = drive\nclient_id = XXX\nclient_secret = XXX\n")
                raise AssertionError(args)
            with patch.object(b,"run_rclone",side_effect=fake_run):
                info=b.doctor(cfg)
            self.assertTrue(info["private_oauth_client_configured"])
            self.assertNotIn("XXX", json.dumps(info))
            self.assertNotIn("client_secret", json.dumps(info))

    def test_health_payload_documents_snapshot_and_staleness_semantics(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"root"; root.mkdir(); state=Path(td)/"state"
            cfg={"remote":"x:","base_path":"Bridge","drive_root_folder_id":"abc","bridge_instance_id":"instance-a","allowed_root":str(root),"state_dir":str(state),"shell":b.DEFAULT_SHELL,"rclone_timeout_seconds":30,"health_seconds":60}
            with patch.object(b,"copy_to_remote_cfg",lambda *a,**k: None):
                b.publish_health(cfg)
            payload=json.loads((state/"health.json").read_text())
            self.assertEqual(payload["state_counter_semantics"],"current_state_dir_journal_snapshot")
            self.assertEqual(payload["health_interval_seconds"],60.0)
            self.assertGreaterEqual(payload["health_stale_after_seconds"],90.0)
            self.assertEqual(payload["publisher_pid"],os.getpid())
            self.assertEqual(payload["state_dir"],str(state.resolve()))

    def test_health_payload_skips_expensive_transport_probe(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"root"; root.mkdir(); state=Path(td)/"state"
            cfg={"remote":"x:","base_path":"Bridge","drive_root_folder_id":"abc","allowed_root":str(root),"state_dir":str(state),"shell":b.DEFAULT_SHELL,"rclone_timeout_seconds":30}
            seen=[]
            with patch.object(b,"copy_to_remote_cfg",lambda local,c,leaf: seen.append((c["rclone_timeout_seconds"],leaf))), patch.object(b,"run_rclone") as rr:
                b.publish_health(cfg)
            rr.assert_not_called()
            self.assertEqual(seen,[(5,"health.json")])

class V5ConcurrencyTests(unittest.TestCase):
    def test_workers_overlap_but_same_request_name_not_double_admitted(self):
        cfg={"remote":"x:","base_path":"Bridge","allowed_root":"/tmp","state_dir":"/tmp/x"}
        started=[]; release=threading.Event(); active=set(); lock=threading.Lock()
        def fake_process(name, _cfg):
            started.append((name,time.monotonic()))
            release.wait(2)
        threads=[]
        with patch.object(b,"process_one",fake_process):
            for name in ["a.json","b.json"]:
                with lock: active.add(name)
                t=threading.Thread(target=b._worker,args=(name,cfg,active,lock)); threads.append(t); t.start()
            deadline=time.monotonic()+1
            while len(started)<2 and time.monotonic()<deadline: time.sleep(.01)
            self.assertEqual({x[0] for x in started},{"a.json","b.json"})
            self.assertLess(abs(started[0][1]-started[1][1]),0.5)
            release.set()
            for t in threads:t.join(2)
        self.assertEqual(active,set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
