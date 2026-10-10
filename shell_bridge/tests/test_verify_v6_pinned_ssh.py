import hashlib
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from verify_v6_pinned_ssh import make_request, verify


class PinnedSSHAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.rid = "v6-ssh-test-001"
        self.raw = json.dumps(make_request(self.rid, "/tmp"),
                              separators=(",", ":"), sort_keys=True).encode()
        self.result = {
            "protocol": 1, "id": self.rid, "kind": "result",
            "request_sha256": hashlib.sha256(self.raw).hexdigest(),
            "status": "completed", "exit_code": 0,
            "stdout_text": "up 4 days",
            "write_scope": {"effective": "trusted_operation"},
            "operator_confirmation": {
                "required": False, "approved": True,
                "mode": "operator_installed_capability",
                "category": "pinned_ssh_readonly",
            },
        }

    def test_exact_installed_ssh_status(self):
        verify(self.raw, self.result, self.rid)
        self.assertEqual(make_request(self.rid, "/tmp")["operation_id"], self.rid)

    def test_fake_bypass_or_missing_installed_consent_rejected(self):
        approval = self.result["operator_confirmation"]
        for field, value in (
            ("required", True), ("approved", False),
            ("mode", "dialog"), ("category", "system"),
        ):
            with self.subTest(field=field):
                old = approval[field]
                approval[field] = value
                with self.assertRaises(ValueError):
                    verify(self.raw, self.result, self.rid)
                approval[field] = old

    def test_result_replay_or_mismatched_hash_rejected(self):
        with self.assertRaises(ValueError):
            verify(self.raw + b"changed", self.result, self.rid)
        with self.assertRaises(ValueError):
            verify(self.raw, self.result, "v6-ssh-other")

    def test_nonzero_exit_or_no_stdout_rejected(self):
        self.result["exit_code"] = 76
        with self.assertRaises(ValueError):
            verify(self.raw, self.result, self.rid)
        self.result["exit_code"] = 0
        self.result["stdout_text"] = ""
        with self.assertRaises(ValueError):
            verify(self.raw, self.result, self.rid)


if __name__ == "__main__":
    unittest.main()
