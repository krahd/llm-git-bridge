import hashlib
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from verify_v6_approval import request, verify
import bridge


class ApprovalAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.rid = "v6-approval-staging-1"
        self.raw = json.dumps(request(self.rid, "/tmp/repo"),
                              sort_keys=True, separators=(",", ":")).encode()
        self.confirm = {"required": True, "category": "trusted_operation",
                        "mode": "dialog", "approved": False,
                        "message": "operator declined elevated request; request was not started"}
        self.base = {"protocol": 1, "id": self.rid,
                     "request_sha256": hashlib.sha256(self.raw).hexdigest(),
                     "status": "rejected", "operator_confirmation": self.confirm}

    def test_generated_canary_is_admissible_under_actual_bridge_schema(self):
        # A mock approval result is useless if request admission rejects the
        # canary before the native popup can open.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            workdir = pathlib.Path(td)
            rid = self.rid
            req = request(rid, str(workdir))
            admitted = bridge.validate_request(
                req, rid + ".json", workdir, max_timeout=300)
            self.assertEqual(admitted["operation_id"], rid)
            self.assertEqual(admitted["trusted_operation"]["action"], "inspect")

    def test_cli_rejects_invalid_request_id_before_upload(self):
        import tempfile
        from verify_v6_approval import main
        with tempfile.TemporaryDirectory() as td:
            outfile = str(pathlib.Path(td) / "request.json")
            self.assertEqual(main(["canary", "create", outfile, "v6-approval-$",
                                   td, "deny"]), 1)
            self.assertFalse(pathlib.Path(outfile).exists())

    def test_real_denial(self):
        verify(self.raw, self.base, self.rid, "deny")

    def test_missing_popup_and_silent_rejection_do_not_qualify(self):
        for key, value in (("mode", "reject"), ("category", "other"),
                           ("message", "operator confirmation timed out")):
            with self.subTest(key=key):
                old = self.confirm[key]
                self.confirm[key] = value
                with self.assertRaises(ValueError):
                    verify(self.raw, self.base, self.rid, "deny")
                self.confirm[key] = old

    def test_approval_requires_executed_read_only_helper(self):
        self.base.update({
            "status": "completed", "exit_code": 0,
            "write_scope": {"effective": "trusted_operation"},
            "stdout_text": json.dumps({
                "kind": "bridge_mailbox_ownership_inventory",
                "mutations_performed": False,
            }),
        })
        self.confirm["approved"] = True
        verify(self.raw, self.base, self.rid, "allow")
        self.base["stdout_text"] = '{"kind":"wrong"}'
        with self.assertRaises(ValueError):
            verify(self.raw, self.base, self.rid, "allow")

    def test_request_and_result_hash_identity(self):
        self.base["request_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            verify(self.raw, self.base, self.rid, "deny")


if __name__ == "__main__":
    unittest.main()
