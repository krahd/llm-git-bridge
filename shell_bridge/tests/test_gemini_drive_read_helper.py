"""Offline acceptance and abuse-case tests for the inert Drive helper."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gemini_drive_read_helper as drive


class GeminiDriveHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.policy_dir = root / "policy"
        self.policy_dir.mkdir(mode=0o700)
        self.config = root / "rclone.conf"
        self.config.write_text("[gemini-pedeciba]\ntype = drive\n", encoding="utf-8")
        self.config.chmod(0o600)
        self.binary = root / "rclone"
        self.binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o700)
        self.policy = {
            "schema": 1,
            "remote": "gemini-pedeciba:",
            "rclone_config": str(self.config),
            "rclone_binary": str(self.binary),
            "rclone_sha256": hashlib.sha256(self.binary.read_bytes()).hexdigest(),
        }
        self.write_policy()

    def write_policy(self):
        p = self.policy_dir / "drive-policy.json"
        p.write_text(json.dumps(self.policy), encoding="utf-8")
        p.chmod(0o600)
        return p

    def test_loads_operator_pinned_policy(self):
        self.assertEqual(drive.load_policy(self.policy_dir), (self.binary, self.config))

    def test_remote_and_binary_are_not_agent_selectable(self):
        for field, value in (("remote", "evil-remote:"),
                             ("rclone_sha256", "a" * 64),
                             ("rclone_binary", "rclone")):
            with self.subTest(field=field):
                old = self.policy[field]
                self.policy[field] = value
                self.write_policy()
                with self.assertRaises(drive.DrivePolicyError):
                    drive.load_policy(self.policy_dir)
                self.policy[field] = old
                self.write_policy()

    def test_symlink_and_public_config_are_denied(self):
        p = self.policy_dir / "drive-policy.json"
        p.unlink()
        p.symlink_to(self.config)
        with self.assertRaises(drive.DrivePolicyError):
            drive.load_policy(self.policy_dir)
        p.unlink()
        self.write_policy()
        self.config.chmod(0o644)
        with self.assertRaises(drive.DrivePolicyError):
            drive.load_policy(self.policy_dir)

    def test_policy_duplicate_keys_denied(self):
        p = self.policy_dir / "drive-policy.json"
        p.write_text('{"schema":1,"schema":1}')
        with self.assertRaises(drive.DrivePolicyError):
            drive.load_policy(self.policy_dir)

    def test_probe_is_only_fixed_document_and_has_no_shell(self):
        raw = b'{"request_id":"external-test-001","status":"completed","output":"BRIDGE-RESULT-84726"}'
        with patch.object(drive, "run_bounded", return_value=raw) as run:
            content = drive.read_document(self.binary, self.config, drive.PROBE_FILE)
        self.assertEqual(content["output"], "BRIDGE-RESULT-84726")
        args = run.call_args.args[0]
        self.assertEqual(args[:3], [str(self.binary), "--config", str(self.config)])
        self.assertEqual(args[3:5], ["cat", "gemini-pedeciba:bridge-external-result-test.txt"])
        self.assertNotIn("sh", args)
        for bad in ("../rclone.conf", "gemini-bridge-request-abc.txt;touch evil", "/etc/passwd"):
            with self.subTest(bad=bad), self.assertRaises(drive.DrivePolicyError):
                drive.read_document(self.binary, self.config, bad)

    def test_request_inspection_is_json_only_and_read_only(self):
        name = "gemini-bridge-request-20261127-b1.txt"
        body = json.dumps({"kind": "bridge_poc_request", "id": "gemini-doc-poc-20261127-b1",
                           "action": "diagnostic_echo", "value": "GEMINI-TO-MAC-58319"}).encode()
        with patch.object(drive, "run_bounded", side_effect=[(name + "\n").encode(), body]) as run:
            items = drive.inspect_requests(self.binary, self.config)
        self.assertEqual(items[0]["request"]["value"], "GEMINI-TO-MAC-58319")
        self.assertEqual(len(run.call_args_list), 2)
        lsf = run.call_args_list[0].args[0]
        self.assertIn("--files-only", lsf)
        self.assertIn("--max-depth", lsf)
        self.assertIn("gemini-bridge-request-*.txt", lsf)
        self.assertEqual(run.call_args_list[1].args[0][4], "gemini-pedeciba:" + name)

    def test_content_injection_and_duplicate_document_keys_denied(self):
        bad_bodies = [
            {"kind":"bridge_poc_request","id":"gemini-doc-poc-a", "action":"shell","value":"ECHO"},
            {"kind":"bridge_poc_request","id":"gemini-doc-poc-a", "action":"diagnostic_echo","value":"x;rm -rf"},
            {"kind":"bridge_poc_request","id":"gemini-doc-poc-a", "action":"diagnostic_echo","value":"ABC", "command":"ls"},
        ]
        for data in bad_bodies:
            with self.subTest(body=data), patch.object(drive, "run_bounded", return_value=json.dumps(data).encode()), self.assertRaises(drive.DrivePolicyError):
                drive.read_document(self.binary, self.config, "gemini-bridge-request-20261127-b1.txt")
        with patch.object(drive, "run_bounded", return_value=b'{"x":1,"x":2}'), self.assertRaises(drive.DrivePolicyError):
            drive.read_document(self.binary, self.config, "gemini-bridge-request-20261127-b1.txt")

    def test_empty_or_malicious_listing_fails_closed(self):
        for names in (b"gemini-bridge-request-one.txt\ngemini-bridge-request-one.txt\n",
                      b"gemini-bridge-request-one.txt\n../../evil.txt\n",
                      ("gemini-bridge-request-x.txt\n" * (drive.MAX_REQUESTS + 1)).encode()):
            with self.subTest(names=names[:30]), patch.object(drive, "run_bounded", return_value=names), self.assertRaises(drive.DrivePolicyError):
                drive.inspect_requests(self.binary, self.config)

    def test_subprocess_output_and_time_are_bounded(self):
        with self.assertRaises(drive.DrivePolicyError):
            drive.run_bounded([sys.executable, "-c", "print('x'*50000)"], max_bytes=256, timeout=3)
        with self.assertRaises(drive.DrivePolicyError):
            drive.run_bounded([sys.executable, "-c", "import time; time.sleep(5)"], max_bytes=256, timeout=1)

    def test_end_to_end_with_fake_rclone_process(self):
        program = """#!/usr/bin/env python3
import sys
if 'lsf' in sys.argv:
    print('gemini-bridge-request-20261127-b1.txt')
elif 'cat' in sys.argv and 'bridge-external-result-test.txt' in ' '.join(sys.argv):
    print('{"request_id":"external-test-001","status":"completed","output":"BRIDGE-RESULT-84726"}')
elif 'cat' in sys.argv and 'gemini-bridge-request-20261127-b1.txt' in ' '.join(sys.argv):
    print('{"kind":"bridge_poc_request","id":"gemini-doc-poc-20261127-b1","action":"diagnostic_echo","value":"GEMINI-TO-MAC-58319"}')
else:
    sys.exit(7)
"""
        self.binary.write_text(program, encoding="utf-8")
        self.binary.chmod(0o700)
        self.policy["rclone_sha256"] = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.write_policy()
        for action, expected in (("probe-test", "BRIDGE-RESULT-84726"),
                                 ("inspect-requests", "GEMINI-TO-MAC-58319")):
            with self.subTest(action=action):
                stream = io.StringIO()
                with contextlib.redirect_stdout(stream):
                    self.assertEqual(drive.main(["helper", action, str(self.policy_dir)]), 0)
                self.assertIn(expected, json.dumps(json.loads(stream.getvalue())))

    def test_rclone_stderr_never_leaks_credentials(self):
        self.binary.write_text("#!/bin/sh\necho 'SECRET-TOKEN-DO-NOT-PRINT' >&2\nexit 3\n")
        self.binary.chmod(0o700)
        self.policy["rclone_sha256"] = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.write_policy()
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            self.assertEqual(drive.main(["helper", "probe-test", str(self.policy_dir)]), 2)
        self.assertIn("PINNED_DRIVE_DENIED", stream.getvalue())
        self.assertNotIn("SECRET-TOKEN", stream.getvalue())

    def test_unknown_action_is_denied_before_policy_access(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(drive.main(["helper", "arbitrary-shell", str(self.policy_dir)]), 2)


if __name__ == "__main__":
    unittest.main()
