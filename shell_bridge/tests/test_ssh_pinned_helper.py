import io
import json
import types
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import ssh_pinned_helper as helper


class PinnedSSHHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=pathlib.Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.root.chmod(0o700)
        self.key = self.root / "identity"
        self.key.write_text("fixture")
        self.key.chmod(0o600)
        self.hosts = self.root / "known_hosts"
        self.hosts.write_text("example.invalid ssh-ed25519 FAKE_FIXTURE\n")
        self.hosts.chmod(0o600)
        self.profile = self.root / "ssh-policy.json"
        self.data = {"schema": 1, "host": "example.invalid", "user": "deploy",
                     "port": 2222, "identity_file": str(self.key)}
        self.write_policy()

    def write_policy(self):
        self.profile.write_text(json.dumps(self.data))
        self.profile.chmod(0o600)

    def test_exact_host_and_read_only_action(self):
        args, timeout = helper.load_policy(self.root, "status")
        self.assertEqual(timeout, 25)
        self.assertIn("deploy@example.invalid", args)
        self.assertEqual(args[-1], "/usr/bin/uptime")
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertIn("UserKnownHostsFile=" + str(self.hosts), args)
        self.assertIn("ProxyCommand=none", args)
        self.assertIn("ClearAllForwardings=yes", args)
        self.assertIn("/dev/null", args)

    def test_refuses_arbitrary_command_or_host(self):
        for action in ["rm", "shell", "status;whoami", "../status", "status --oops"]:
            with self.subTest(action=action):
                with self.assertRaises(helper.SSHPolicyError):
                    helper.load_policy(self.root, action)
        for hostname in ["-oProxyCommand=evil", "example.invalid;id", "host name"]:
            with self.subTest(hostname=hostname):
                self.data["host"] = hostname
                self.write_policy()
                with self.assertRaises(helper.SSHPolicyError):
                    helper.load_policy(self.root, "status")

    def test_refuses_symlink_and_writable_policy(self):
        self.profile.chmod(0o666)
        with self.assertRaises(helper.SSHPolicyError):
            helper.load_policy(self.root, "status")
        self.profile.chmod(0o600)
        self.hosts.unlink()
        self.hosts.symlink_to(self.profile)
        with self.assertRaises(helper.SSHPolicyError):
            helper.load_policy(self.root, "status")

    def test_refuses_unexpected_policy_fields(self):
        self.data["command"] = "rm -rf /"
        self.write_policy()
        with self.assertRaises(helper.SSHPolicyError):
            helper.load_policy(self.root, "status")

    def test_remote_failure_and_timeout_never_retry(self):
        with patch.object(helper.subprocess, "run", side_effect=helper.subprocess.TimeoutExpired("ssh", 25)) as run:
            self.assertEqual(helper.execute("status", str(self.root)), 76)
            self.assertEqual(run.call_count, 1)

    def test_remote_success_is_bounded(self):
        response = helper.subprocess.CompletedProcess([], 0, stdout=b"healthy", stderr=b"")
        with patch.object(helper.subprocess, "run", return_value=response) as run:
            with patch.object(helper.sys, "stdout", types.SimpleNamespace(buffer=io.BytesIO())):
                self.assertEqual(helper.execute("status", str(self.root)), 0)
            self.assertEqual(run.call_count, 1)
            self.assertFalse(run.call_args.kwargs["check"])


if __name__ == "__main__":
    unittest.main()
