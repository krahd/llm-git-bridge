import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge as b


class ShellBridgeTransportReliabilityTests(unittest.TestCase):
    def _cfg(self, td: str) -> dict:
        root = Path(td) / "root"
        root.mkdir(exist_ok=True)
        state = Path(td) / "state"
        return {
            "remote": "x:",
            "base_path": "Bridge",
            "drive_root_folder_id": "root123",
            "allowed_root": str(root),
            "state_dir": str(state),
            "shell": b.DEFAULT_SHELL,
            "rclone_timeout_seconds": 30,
        }

    def test_poll_subprocess_fallback_is_capped_at_five_seconds(self):
        class CP:
            returncode = 0
            stdout = b""
            stderr = b""
        seen = []
        def fake_run(argv, **kwargs):
            seen.append(kwargs.get("timeout"))
            return CP()
        with tempfile.TemporaryDirectory() as td:
            cfg = self._cfg(td)
            with patch.object(b.subprocess, "run", fake_run):
                self.assertEqual(b.list_requests_cfg(cfg), [])
        self.assertEqual(len(seen), 1)
        self.assertLessEqual(seen[0], 5)

    def test_rc_list_bypasses_subprocess_when_socket_is_ready(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = self._cfg(td)
            socket_path = Path(cfg["state_dir"]) / "rclone-rc.sock"
            socket_path.parent.mkdir(parents=True)
            socket_path.touch()
            response = {"list": [
                {"Name": "b.json", "IsDir": False},
                {"Name": "a.json", "IsDir": False},
            ]}
            with patch.object(b, "_rc_request", return_value=response) as rc, \
                 patch.object(b, "run_rclone", side_effect=AssertionError("subprocess fallback used")):
                self.assertEqual(b.list_requests_cfg(cfg), ["a.json", "b.json"])
            self.assertLessEqual(rc.call_args.kwargs["timeout"], 3)

    def test_rc_write_timeout_does_not_fallback_to_subprocess(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = self._cfg(td)
            socket_path = Path(cfg["state_dir"]) / "rclone-rc.sock"
            socket_path.parent.mkdir(parents=True)
            socket_path.touch()
            local = Path(td) / "result.json"
            local.write_text("{}")
            with patch.object(b, "_rc_request", side_effect=RuntimeError("timed out")), \
                 patch.object(b, "run_rclone", side_effect=AssertionError("unsafe fallback")):
                with self.assertRaisesRegex(RuntimeError, "unknown"):
                    b.copy_to_remote_cfg(local, cfg, "results/result.json")

    def test_finished_retry_skips_reupload_when_remote_result_exists(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = self._cfg(td)
            local = Path(td) / "result.json"
            local.write_text(json.dumps({"status": "completed"}))
            deleted = []
            with patch.object(b, "_remote_leaf_exists_cfg", return_value=True), \
                 patch.object(b, "copy_to_remote_cfg", side_effect=AssertionError("duplicate upload")), \
                 patch.object(b, "delete_remote_cfg", side_effect=lambda c, leaf: deleted.append(leaf)):
                b._publish_stored(local, "request.json", cfg)
            self.assertEqual(deleted, ["requests/request.json"])


if __name__ == "__main__":
    unittest.main()


class ShellBridgeRcloneLifecycleTests(unittest.TestCase):
    def _cfg(self, td: str) -> dict:
        root = Path(td) / "root"
        root.mkdir(exist_ok=True)
        return {
            "remote": "x:",
            "base_path": "Bridge",
            "drive_root_folder_id": "root123",
            "allowed_root": str(root),
            "state_dir": str(Path(td) / "state"),
            "shell": b.DEFAULT_SHELL,
            "rclone_timeout_seconds": 30,
            "poll_seconds": 0.01,
            "health_seconds": 60,
        }

    def test_start_rcd_preserves_drive_root_pin_and_uses_unix_socket(self):
        class Proc:
            def poll(self): return None
            def terminate(self): pass
            def wait(self, timeout=None): return 0
            def kill(self): pass
        seen = []
        def fake_popen(argv, **kwargs):
            seen.append(argv)
            addr = argv[argv.index("--rc-addr") + 1]
            Path(addr.removeprefix("unix://")).touch()
            return Proc()
        with tempfile.TemporaryDirectory() as td:
            cfg = self._cfg(td)
            with patch.object(b.shutil, "which", return_value="/usr/local/bin/rclone"), \
                 patch.object(b, "_retire_existing_rcd", lambda *a, **k: None), \
                 patch.object(b, "_rc_ready", return_value=True), \
                 patch.object(b.subprocess, "Popen", fake_popen):
                rcd = b.start_rclone_rcd(cfg, startup_timeout=0.2)
            self.assertIsNotNone(rcd)
            self.assertIn("--drive-root-folder-id", seen[0])
            self.assertEqual(seen[0][seen[0].index("--drive-root-folder-id") + 1], "root123")
            self.assertIn("rcd", seen[0])
            self.assertTrue(any(x.startswith("unix://") for x in seen[0]))

    def test_daemon_starts_and_stops_owned_rcd(self):
        class Lock:
            def close(self): pass
        class Rcd:
            health_failures = 0
            def healthy(self, timeout=0.25): return True
            def stop(self): self.stopped = True
        with tempfile.TemporaryDirectory() as td:
            cfg = self._cfg(td)
            rcd = Rcd(); rcd.stopped = False
            def one_poll(_cfg):
                b._SHUTDOWN.set()
                return []
            with patch.object(b, "load_config", return_value=cfg), \
                 patch.object(b, "acquire_instance_lock", return_value=Lock()), \
                 patch.object(b, "ensure_remote_dirs"), \
                 patch.object(b, "sweep_incomplete_processes", return_value=0), \
                 patch.object(b, "start_rclone_rcd", return_value=rcd) as start, \
                 patch.object(b, "list_requests_cfg", side_effect=one_poll), \
                 patch.object(b, "publish_health"):
                b.daemon(Path(td) / "config.json")
            start.assert_called_once_with(cfg)
            self.assertTrue(rcd.stopped)

    def test_two_rc_health_misses_restart_rcd(self):
        class Lock:
            def close(self): pass
        class Rcd:
            def __init__(self, health): self.health=list(health); self.stopped=False
            def healthy(self, timeout=0.25): return self.health.pop(0) if self.health else True
            def stop(self): self.stopped=True
        with tempfile.TemporaryDirectory() as td:
            cfg = self._cfg(td)
            cfg["rc_health_seconds"] = 0.01
            first = Rcd([False, False])
            second = Rcd([True])
            starts = [first, second]
            polls = {"n": 0}
            def list_once(_cfg):
                polls["n"] += 1
                if polls["n"] >= 4:
                    b._SHUTDOWN.set()
                time.sleep(0.012)
                return []
            with patch.object(b, "load_config", return_value=cfg), \
                 patch.object(b, "acquire_instance_lock", return_value=Lock()), \
                 patch.object(b, "ensure_remote_dirs"), \
                 patch.object(b, "sweep_incomplete_processes", return_value=0), \
                 patch.object(b, "start_rclone_rcd", side_effect=starts) as start, \
                 patch.object(b, "list_requests_cfg", side_effect=list_once), \
                 patch.object(b, "publish_health"):
                b.daemon(Path(td) / "config.json")
            self.assertGreaterEqual(start.call_count, 2)
            self.assertTrue(first.stopped)
