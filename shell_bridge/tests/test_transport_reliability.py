import json
import os
import tempfile
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
