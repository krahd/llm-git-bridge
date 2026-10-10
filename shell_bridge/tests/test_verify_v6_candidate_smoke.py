import hashlib
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from verify_v6_candidate_smoke import SmokeVerificationError, verify


class CandidateSmokeVerificationTests(unittest.TestCase):
    def setUp(self):
        self.rid = "v6-smoke-20261010-42"
        self.raw = b'{"command":"printf ISOLATED_V6_SMOKE_OK","id":"v6-smoke-20261010-42"}'
        self.result = {
            "protocol": 1, "kind": "result", "id": self.rid,
            "status": "completed", "exit_code": 0,
            "request_sha256": hashlib.sha256(self.raw).hexdigest(),
            "stdout_text": "ISOLATED_V6_SMOKE_OK",
            "filesystem_sandboxed": True, "network_sandboxed": True,
            "write_scope": {"effective": "read_only"},
        }

    def test_success(self):
        verify(self.raw, self.result, self.rid)

    def test_hash_mismatch_or_replay_rejected(self):
        with self.assertRaises(SmokeVerificationError):
            verify(self.raw + b"changed", self.result, self.rid)
        with self.assertRaises(SmokeVerificationError):
            verify(self.raw, self.result, "another-request")

    def test_requires_completed_zero_exit_code_and_actual_sandbox(self):
        for field, value in (
            ("status", "indeterminate"), ("exit_code", 1),
            ("filesystem_sandboxed", False), ("network_sandboxed", False),
            ("stdout_text", "not the marker"),
        ):
            with self.subTest(field=field):
                old = self.result[field]
                self.result[field] = value
                with self.assertRaises(SmokeVerificationError):
                    verify(self.raw, self.result, self.rid)
                self.result[field] = old

    def test_requires_read_only_execution(self):
        self.result["write_scope"]["effective"] = "system"
        with self.assertRaises(SmokeVerificationError):
            verify(self.raw, self.result, self.rid)


if __name__ == "__main__":
    unittest.main()
