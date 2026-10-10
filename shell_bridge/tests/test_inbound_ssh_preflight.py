import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import inbound_ssh_preflight as inbound


class InboundSSHPreflightTests(unittest.TestCase):
    def test_remote_login_and_restricted_current_user_required(self):
        result = inbound.verify(
            "Remote Login: On\n",
            "yes tom is a member of com.apple.access_ssh\n", "tom")
        self.assertTrue(result["remote_login_on"])
        self.assertTrue(result["account_explicitly_allowed"])
        self.assertFalse(result["external_connectivity_verified"])
        self.assertFalse(result["host_authentication_verified"])
        self.assertFalse(result["mutations_performed"])

    def test_remote_login_off_or_ambiguous_is_rejected(self):
        for output in ("Remote Login: Off", "", "Remote Login: On\nRemote Login: Off"):
            with self.subTest(output=output):
                with self.assertRaises(ValueError):
                    inbound.verify(output, "yes tom is a member of com.apple.access_ssh", "tom")

    def test_missing_or_unexpected_user_membership_is_rejected(self):
        for response in (
            "no tom is not a member of com.apple.access_ssh",
            "yes other is a member of com.apple.access_ssh",
            "", "yes tom is a member of com.apple.access_ssh\nextraneous",
        ):
            with self.subTest(response=response):
                with self.assertRaises(ValueError):
                    inbound.verify("Remote Login: On", response, "tom")

    def test_preflight_does_not_enable_remote_login(self):
        script = (pathlib.Path(__file__).resolve().parents[1]
                  / "inbound_ssh_preflight.py").read_text()
        self.assertIn('"-getremotelogin"', script)
        self.assertIn('"checkmember"', script)
        self.assertNotIn("-setremotelogin", script)
        self.assertNotIn("ssh-keyscan", script)


if __name__ == "__main__":
    unittest.main()
