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


class V5CrashTests(unittest.TestCase):
    def test_started_active_is_contained_before_indeterminate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)/"root"; root.mkdir()
            state = Path(td)/"state"
            rdir = state/"requests"/"jobx"; rdir.mkdir(parents=True)
            raw = json.dumps({"protocol":1,"id":"jobx","cwd":str(root),"command":"true"}).encode()
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
