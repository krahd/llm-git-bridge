import base64
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


class ValidationTests(unittest.TestCase):
    def test_accepts_nested_repo(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "projects" / "repo"
            repo.mkdir(parents=True)
            req = {"protocol": 1, "id": "abc-123", "cwd": str(repo), "command": "git status", "timeout_seconds": 10}
            out = b.validate_request(req, "abc-123.json", root, 300)
            self.assertEqual(out["cwd"], repo.resolve())

    def test_rejects_outside_root_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as other:
            root = Path(td)
            req = {"protocol": 1, "id": "abc", "cwd": other, "command": "pwd"}
            with self.assertRaisesRegex(ValueError, "allowed_root"):
                b.validate_request(req, "abc.json", root, 300)
            link = root / "escape"
            try:
                link.symlink_to(other, target_is_directory=True)
            except OSError:
                return
            req["cwd"] = str(link)
            with self.assertRaisesRegex(ValueError, "allowed_root"):
                b.validate_request(req, "abc.json", root, 300)

    def test_id_and_filename_are_strict_and_traversal_safe(self):
        good = ["a.json", "abc-123.json", "x.y_z.json", "0.json"]
        for name in good:
            self.assertEqual(b.validate_request_name(name), name[:-5])
        bad = ["...json", "..json", ".json", "A.json", "a b.json", "../x.json", "x/.json", "é.json", "a" * 129 + ".json"]
        for name in bad:
            with self.assertRaises(ValueError, msg=name):
                b.validate_request_name(name)

    def test_filename_must_match_id(self):
        with tempfile.TemporaryDirectory() as td:
            req = {"protocol": 1, "id": "abc", "cwd": td, "command": "pwd"}
            with self.assertRaisesRegex(ValueError, "filename"):
                b.validate_request(req, "xyz.json", Path(td), 300)

    def test_timeout_bool_and_payload_limits_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = {"protocol": 1, "id": "abc", "cwd": td, "command": "pwd"}
            req = dict(base, timeout_seconds=True)
            with self.assertRaisesRegex(ValueError, "timeout_seconds"):
                b.validate_request(req, "abc.json", root, 300)
            req = dict(base, command="x" * 11)
            with self.assertRaisesRegex(ValueError, "command is too large"):
                b.validate_request(req, "abc.json", root, 300, max_command_bytes=10)
            req = dict(base, stdin_b64=base64.b64encode(b"12345").decode())
            with self.assertRaisesRegex(ValueError, "stdin is too large"):
                b.validate_request(req, "abc.json", root, 300, max_stdin_bytes=4)


class ShellTests(unittest.TestCase):
    def test_binary_stdout_stderr_and_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as td:
            cmd = 'python3 -c "import sys; sys.stdout.buffer.write(bytes([97,0,98])); sys.stderr.buffer.write(bytes([101,114,114,255])); raise SystemExit(7)"'
            out = b.run_shell(Path(td), cmd, b"", 10)
            self.assertEqual(out["exit_code"], 7)
            self.assertEqual(base64.b64decode(out["stdout_b64"]), b"a\x00b")
            self.assertEqual(base64.b64decode(out["stderr_b64"]), b"err\xff")
            self.assertEqual(out["stdout_sha256"], b.sha256_bytes(b"a\x00b"))
            self.assertFalse(out["timed_out"])
            self.assertFalse(out["output_limited"])

    def test_stdin_exact(self):
        with tempfile.TemporaryDirectory() as td:
            out = b.run_shell(Path(td), "cat", b"hello\x00world", 10)
            self.assertEqual(base64.b64decode(out["stdout_b64"]), b"hello\x00world")

    def test_timeout_does_not_block_on_unread_stdin(self):
        with tempfile.TemporaryDirectory() as td:
            started = time.monotonic()
            out = b.run_shell(Path(td), "sleep 10", b"x" * (1024 * 1024), 1)
            elapsed = time.monotonic() - started
            self.assertTrue(out["timed_out"])
            self.assertLess(elapsed, 4.0)

    def test_timeout_kills_descendant_that_ignores_sigterm(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            marker = td / "SURVIVED"
            child = td / "child.py"
            child.write_text(
                "import signal,time,pathlib\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "time.sleep(2)\n"
                f"pathlib.Path({str(marker)!r}).write_text('bad')\n"
            )
            parent = td / "parent.py"
            parent.write_text(
                "import subprocess,time\n"
                f"subprocess.Popen(['python3',{str(child)!r}])\n"
                "time.sleep(20)\n"
            )
            out = b.run_shell(td, f"python3 {parent.name}", b"", 1)
            self.assertTrue(out["timed_out"])
            time.sleep(2.5)
            self.assertFalse(marker.exists())

    def test_output_limit_bounds_and_terminates(self):
        with tempfile.TemporaryDirectory() as td:
            cmd = 'python3 -c "import sys; sys.stdout.write(\'x\'*1000000); sys.stdout.flush()"'
            out = b.run_shell(Path(td), cmd, b"", 10, max_output_bytes=4096)
            self.assertTrue(out["output_limited"])
            self.assertLessEqual(out["stdout_bytes"], 4096)

    def test_nonexistent_shell_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError, "configured shell"):
                b.run_shell(Path(td), "true", b"", 1, shell="/definitely/not/a/shell")


class ProcessTests(unittest.TestCase):
    def make_cfg(self, root, state, **extra):
        cfg = {
            "remote": "x:", "base_path": "Bridge", "state_dir": str(state),
            "allowed_root": str(root), "max_timeout_seconds": 30,
            "shell": b.DEFAULT_SHELL,
        }
        cfg.update(extra)
        return cfg

    def run_with_remote(self, name, raw, cfg, fail_upload_once=False):
        uploaded, deleted = {}, []
        calls = {"shell": 0, "upload": 0}
        original_run = b.run_shell
        def fake_from(remote, base, leaf, local): Path(local).write_bytes(raw)
        def fake_to(local, remote, base, leaf):
            calls["upload"] += 1
            if fail_upload_once and calls["upload"] == 1:
                raise RuntimeError("simulated upload ambiguity")
            uploaded[leaf] = Path(local).read_bytes()
        def fake_delete(remote, base, leaf): deleted.append(leaf)
        def counted_run(*args, **kwargs):
            calls["shell"] += 1
            return original_run(*args, **kwargs)
        with patch.object(b, "copy_from_remote", fake_from), patch.object(b, "copy_to_remote", fake_to), \
             patch.object(b, "delete_remote", fake_delete), patch.object(b, "run_shell", counted_run):
            try:
                b.process_one(name, cfg)
                error = None
            except Exception as exc:
                error = exc
        return uploaded, deleted, calls, error

    def test_completed_replay_is_hash_bound_and_no_reexecution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            state = Path(td) / "state"
            cfg = self.make_cfg(root, state)
            raw = json.dumps({"protocol":1,"id":"job1","cwd":str(root),"command":"printf hello","timeout_seconds":10}).encode()
            up, deleted, calls, err = self.run_with_remote("job1.json", raw, cfg)
            self.assertIsNone(err); self.assertEqual(calls["shell"], 1)
            first = json.loads(up["results/job1.json"])
            self.assertEqual(first["status"], "completed")
            up2, _, calls2, err2 = self.run_with_remote("job1.json", raw, cfg)
            self.assertIsNone(err2); self.assertEqual(calls2["shell"], 0)
            self.assertEqual(json.loads(up2["results/job1.json"])["request_sha256"], first["request_sha256"])
            changed = json.dumps({"protocol":1,"id":"job1","cwd":str(root),"command":"printf CHANGED","timeout_seconds":10}).encode()
            up3, _, calls3, err3 = self.run_with_remote("job1.json", changed, cfg)
            self.assertIsNone(err3); self.assertEqual(calls3["shell"], 0)
            conflict = json.loads(up3["results/job1.json"])
            self.assertEqual(conflict["status"], "rejected")
            self.assertIn("reused", conflict["message"])

    def test_started_without_finished_is_indeterminate_and_hash_bound(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            state = Path(td) / "state"
            rdir = state / "requests" / "job2"; rdir.mkdir(parents=True)
            raw = json.dumps({"protocol":1,"id":"job2","cwd":str(root),"command":"touch SHOULD_NOT_EXIST"}).encode()
            (rdir / "request.json").write_bytes(raw)
            (rdir / "started.json").write_text(json.dumps({"request_sha256": b.sha256_bytes(raw)}))
            cfg = self.make_cfg(root, state)
            up, _, calls, err = self.run_with_remote("job2.json", raw, cfg)
            self.assertIsNone(err); self.assertEqual(calls["shell"], 0)
            self.assertEqual(json.loads(up["results/job2.json"])["status"], "indeterminate")
            self.assertFalse((root / "SHOULD_NOT_EXIST").exists())

    def test_upload_failure_after_execution_replays_without_execution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            state = Path(td) / "state"
            cfg = self.make_cfg(root, state)
            raw = json.dumps({"protocol":1,"id":"job3","cwd":str(root),"command":"printf once","timeout_seconds":10}).encode()
            _, _, calls, err = self.run_with_remote("job3.json", raw, cfg, fail_upload_once=True)
            self.assertIsInstance(err, RuntimeError)
            self.assertEqual(calls["shell"], 1)
            self.assertTrue((state / "requests" / "job3" / "finished.json").exists())
            up2, _, calls2, err2 = self.run_with_remote("job3.json", raw, cfg)
            self.assertIsNone(err2); self.assertEqual(calls2["shell"], 0)
            self.assertEqual(json.loads(up2["results/job3.json"])["stdout_text"], "once")

    def test_malformed_json_is_terminal_rejection(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            state = Path(td) / "state"
            cfg = self.make_cfg(root, state)
            up, _, calls, err = self.run_with_remote("bad1.json", b"{not-json", cfg)
            self.assertIsNone(err); self.assertEqual(calls["shell"], 0)
            result = json.loads(up["results/bad1.json"])
            self.assertEqual(result["status"], "rejected")
            self.assertTrue((state / "requests" / "bad1" / "finished.json").exists())

    def test_oversize_request_rejected_without_loading_or_execution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            state = Path(td) / "state"
            cfg = self.make_cfg(root, state, max_request_bytes=10)
            raw = b"x" * 100
            up, _, calls, err = self.run_with_remote("big1.json", raw, cfg)
            self.assertIsNone(err); self.assertEqual(calls["shell"], 0)
            result = json.loads(up["results/big1.json"])
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(result["request_sha256"], b.sha256_bytes(raw))


class ConfigAndLockTests(unittest.TestCase):
    def test_config_validation_and_single_instance_lock(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            state = Path(td) / "state"
            cfg_path = Path(td) / "cfg.json"
            cfg = {"remote":"x:","base_path":"Bridge","allowed_root":str(root),"state_dir":str(state),"shell":b.DEFAULT_SHELL}
            cfg_path.write_text(json.dumps(cfg))
            loaded = b.load_config(cfg_path)
            lock1 = b.acquire_instance_lock(loaded)
            try:
                with self.assertRaisesRegex(RuntimeError, "another"):
                    b.acquire_instance_lock(loaded)
            finally:
                lock1.close()
            cfg["poll_seconds"] = 0
            cfg_path.write_text(json.dumps(cfg))
            with self.assertRaisesRegex(ValueError, "poll_seconds"):
                b.load_config(cfg_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
