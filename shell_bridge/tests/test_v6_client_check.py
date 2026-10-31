"""Portable offline acceptance checks for v6 agent request/result envelopes."""
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "skills" / "mac-git-bridge" / "scripts" / "v6_client_check.py"
spec = importlib.util.spec_from_file_location("v6_client_check", PATH)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class V6ClientCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.req = self.root / "client-check-001.json"
        self.request = {"protocol": 1, "id": "client-check-001", "cwd": "/Users/example/repos/demo",
                        "command": "git status --short", "explanation": "Inspect git status.",
                        "timeout_seconds": 30, "write_scope": "read_only"}
        self.req.write_text(json.dumps(self.request))

    def result(self, **changes):
        data = {"protocol": 1, "kind": "result", "id": self.request["id"], "status": "completed",
                "request_sha256": hashlib.sha256(self.req.read_bytes()).hexdigest(), "exit_code": 0}
        data.update(changes)
        p = self.root / "result" / self.req.name
        p.parent.mkdir(exist_ok=True)
        p.write_text(json.dumps(data))
        return p

    def test_healthy_request_and_result(self):
        self.assertEqual(checker.check_request(self.req)["id"], "client-check-001")
        self.assertTrue(checker.check_result(self.result(), self.req)["request_hash_verified"])

    def test_nonzero_exit_is_not_success(self):
        with self.assertRaisesRegex(checker.CheckError, "command failed"):
            checker.check_result(self.result(exit_code=2), self.req)

    def test_indeterminate_is_not_success(self):
        with self.assertRaisesRegex(checker.CheckError, "did not complete"):
            checker.check_result(self.result(status="indeterminate"), self.req)

    def test_digest_mismatch_is_rejected(self):
        with self.assertRaisesRegex(checker.CheckError, "hash"):
            checker.check_result(self.result(request_sha256="0" * 64), self.req)

    def test_duplicate_keys_rejected(self):
        self.req.write_text('{"id":"client-check-001","id":"client-check-001"}')
        with self.assertRaisesRegex(checker.CheckError, "duplicate JSON"):
            checker.check_request(self.req)

    def test_bad_explanation_rejected(self):
        self.request["explanation"] = " "
        self.req.write_text(json.dumps(self.request))
        with self.assertRaisesRegex(checker.CheckError, "explanation"):
            checker.check_request(self.req)

    def test_boolean_timeout_rejected(self):
        self.request["timeout_seconds"] = True
        self.req.write_text(json.dumps(self.request))
        with self.assertRaisesRegex(checker.CheckError, "timeout"):
            checker.check_request(self.req)

    def test_mismatched_filename_rejected(self):
        self.request["id"] = "other-id"
        self.req.write_text(json.dumps(self.request))
        with self.assertRaisesRegex(checker.CheckError, "filename"):
            checker.check_request(self.req)

    def test_incomplete_result_rejected(self):
        with self.assertRaisesRegex(checker.CheckError, "incomplete"):
            checker.check_result(self.result(output_limited=True), self.req)

    def test_unhashable_scope_and_status_are_rejected(self):
        self.request["write_scope"] = []
        self.req.write_text(json.dumps(self.request))
        with self.assertRaisesRegex(checker.CheckError, "write_scope"):
            checker.check_request(self.req)
        self.request["write_scope"] = "read_only"
        self.req.write_text(json.dumps(self.request))
        with self.assertRaisesRegex(checker.CheckError, "status"):
            checker.check_result(self.result(status=[]), self.req)

    def test_trusted_operation_needs_id_and_auto_scope(self):
        self.request.pop("command")
        self.request["trusted_operation"] = {"name": "known", "action": "inspect"}
        self.request["operation_id"] = "unique-op-001"
        self.request["write_scope"] = "auto"
        self.req.write_text(json.dumps(self.request))
        self.assertEqual(checker.check_request(self.req)["id"], "client-check-001")
        self.request["write_scope"] = "repository"
        self.req.write_text(json.dumps(self.request))
        with self.assertRaisesRegex(checker.CheckError, "auto scope"):
            checker.check_request(self.req)

    def test_good_health_and_wrong_version(self):
        health = self.root / "health.json"
        sample = {"bridge_version": "6", "build_code_integrity": "matches_manifest",
                  "drive_root_folder_id": "root", "requests_folder_id": "requests", "results_folder_id": "results",
                  "state": {"started_without_finished": 0}}
        health.write_text(json.dumps(sample))
        self.assertTrue(checker.check_health(health)["pinned_folders_present"])
        sample["bridge_version"] = "5"
        health.write_text(json.dumps(sample))
        with self.assertRaisesRegex(checker.CheckError, "not a v6"):
            checker.check_health(health)


if __name__ == "__main__":
    unittest.main()
