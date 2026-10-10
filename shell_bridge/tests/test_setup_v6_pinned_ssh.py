import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from setup_v6_pinned_ssh import EnrollmentError, enroll, trusted_host_lines


class SSHEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=pathlib.Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.root.chmod(0o700)
        self.key = self.root / "private-key"
        self.key.write_text("fixture key")
        self.key.chmod(0o600)
        self.directory = self.root / "enrolled"
        self.keys = "example.invalid ssh-ed25519 FAKE_FIXTURE" + chr(10)

    def test_exact_pinned_host_and_no_override(self):
        result = enroll(self.directory, self.key, "example.invalid",
                        "deploy", 22, True, self.keys)
        self.assertEqual(result, self.directory)
        self.assertEqual((result / "known_hosts").read_text(), self.keys)
        self.assertEqual(json.loads((result / "ssh-policy.json").read_text())["host"],
                         "example.invalid")
        self.assertEqual((result / "known_hosts").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(EnrollmentError):
            enroll(self.directory, self.key, "example.invalid",
                   "deploy", 22, True, self.keys)

    def test_denied_or_unsafe_operator_inputs_fail_before_creation(self):
        for name, user, confirm in (
            ("example.invalid", "deploy", False),
            ("-oProxyCommand=evil", "deploy", True),
            ("example.invalid", "bad user", True),
        ):
            with self.subTest(name=name, user=user, confirm=confirm):
                with self.assertRaises(EnrollmentError):
                    enroll(self.directory, self.key, name, user, 22,
                           confirm, self.keys)
                self.assertFalse(self.directory.exists())

    def test_private_key_must_be_owner_only(self):
        self.key.chmod(0o644)
        with self.assertRaises(EnrollmentError):
            enroll(self.directory, self.key, "example.invalid",
                   "deploy", 22, True, self.keys)

    def test_existing_host_key_is_required_and_never_fetched(self):
        source = self.root / "known_hosts"
        source.write_text(self.keys)
        source.chmod(0o600)
        with patch("setup_v6_pinned_ssh.subprocess.run") as call:
            call.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="")
            with self.assertRaises(EnrollmentError):
                trusted_host_lines(source, "example.invalid", 22)
            self.assertEqual(call.call_count, 1)
            self.assertEqual(call.call_args.args[0][1], "-F")
            self.assertNotIn("ssh-keyscan", str(call.call_args))

    def test_previewed_keys_are_written_without_later_network_lookup(self):
        with patch("setup_v6_pinned_ssh.subprocess.run") as call:
            enroll(self.directory, self.key, "example.invalid",
                   "deploy", 22, True, self.keys)
            self.assertEqual(call.call_count, 0)


if __name__ == "__main__":
    unittest.main()
