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
        self.assertIn("GlobalKnownHostsFile=/dev/null", args)
        self.assertIn("UpdateHostKeys=no", args)
        self.assertIn("VerifyHostKeyDNS=no", args)
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

    def test_operator_pinned_remote_argv_uses_shell_quoting(self):
        self.data["schema"] = 2
        self.data["commands"] = {
            "repo-status": ["/usr/bin/git", "-C", "/srv/app folder", "status", "--short", "$(touch /tmp/not-executed)"],
            "service-restart": ["/bin/systemctl", "restart", "app.service"],
        }
        self.write_policy()
        argv, timeout = helper.load_policy(self.root, "repo-status")
        self.assertEqual(timeout, 25)
        self.assertEqual(argv[-1], "/usr/bin/git -C '/srv/app folder' status --short '$(touch /tmp/not-executed)'")
        self.assertEqual(helper.load_policy(self.root, "service-restart")[0][-1],
                         "/bin/systemctl restart app.service")
        with self.assertRaises(helper.SSHPolicyError):
            helper.load_policy(self.root, "other")

    def test_profile_rejects_unsafe_and_unreviewed_commands(self):
        self.data["schema"] = 2
        for commands in [
            {"status": ["/bin/true"]},
            {"run": ["relative-executable", "arg"]},
            {"run": ["/bin/echo", "bad\\narg"]},
            {"run": "/bin/echo"},
            {"run": []},
            {"run": ["/bin/../bin/sh"]},
            {"run": ["/bin/sh", "bad\\x00arg"]},
            {"run": ["/bin/echo", 23]},
        ]:
            with self.subTest(commands=commands):
                self.data["commands"] = commands
                self.write_policy()
                with self.assertRaises(helper.SSHPolicyError):
                    helper.load_policy(self.root, "run")

    def test_refuses_symlink_and_writable_policy(self):
        self.profile.chmod(0o666)
        with self.assertRaises(helper.SSHPolicyError):
            helper.load_policy(self.root, "status")
        self.profile.chmod(0o600)
        self.hosts.unlink()
        self.hosts.symlink_to(self.profile)
        with self.assertRaises(helper.SSHPolicyError):
            helper.load_policy(self.root, "status")

    def test_private_key_permissions_must_be_owner_only(self):
        self.key.chmod(0o644)
        with self.assertRaises(helper.SSHPolicyError):
            helper.load_policy(self.root, "status")

    def test_refuses_boolean_schema_version(self):
        self.data["schema"] = True
        self.write_policy()
        with self.assertRaises(helper.SSHPolicyError):
            helper.load_policy(self.root, "status")

    def test_refuses_unexpected_policy_fields(self):
        self.data["command"] = "rm -rf /"
        self.write_policy()
        with self.assertRaises(helper.SSHPolicyError):
            helper.load_policy(self.root, "status")

    def test_remote_failure_and_timeout_never_retry(self):
        with patch.object(helper, "run_bounded",
                          side_effect=helper.subprocess.TimeoutExpired("ssh", 25)) as run:
            self.assertEqual(helper.execute("status", str(self.root)), 76)
            self.assertEqual(run.call_count, 1)

    def test_remote_success_is_bounded(self):
        with patch.object(helper, "run_bounded", return_value=(0, b"healthy", b"")) as run:
            output = io.BytesIO()
            with patch.object(helper.sys, "stdout", types.SimpleNamespace(buffer=output)):
                self.assertEqual(helper.execute("status", str(self.root)), 0)
            self.assertEqual(output.getvalue(), b"healthy")
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[1], 25)

    def test_streaming_output_limit_prevents_unbounded_stdout(self):
        argv = [sys.executable, "-c",
                "import sys; sys.stdout.buffer.write(b'x' * 1000000)"]
        with patch.object(helper, "_MAX_BYTES", 2048):
            with self.assertRaises(helper.SSHOutputLimit):
                helper.run_bounded(argv, timeout=3)

    def test_streaming_output_limit_applies_to_stderr(self):
        argv = [sys.executable, "-c",
                "import sys; sys.stderr.buffer.write(b'x' * 1000000)"]
        with patch.object(helper, "_MAX_BYTES", 2048):
            with self.assertRaises(helper.SSHOutputLimit):
                helper.run_bounded(argv, timeout=3)

    def test_local_child_is_killed_on_timeout(self):
        argv = [sys.executable, "-c", "import time; time.sleep(20)"]
        with self.assertRaises(helper.subprocess.TimeoutExpired):
            helper.run_bounded(argv, timeout=1)

    def test_short_local_command_exits_and_drains_both_streams(self):
        argv = [sys.executable, "-c",
                "import sys; sys.stdout.write('ok'); sys.stderr.write('note')"]
        code, out, err = helper.run_bounded(argv, timeout=3)
        self.assertEqual((code, out, err), (0, b"ok", b"note"))


if __name__ == "__main__":
    unittest.main()
