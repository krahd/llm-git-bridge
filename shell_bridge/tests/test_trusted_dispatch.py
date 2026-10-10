import hashlib
import pathlib
import tempfile
import unittest
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bridge import resolve_write_plan, run_shell, validate_request
from trusted_operations import TrustedOperationPolicyError


class TrustedDispatchTests(unittest.TestCase):
    def test_typed_dispatch_and_direct_exec(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp).resolve()
            root = base / "repo"
            root.mkdir()
            executable = base / "helper"
            executable.write_text("#!/bin/sh\\nprintf 'SAFE:%s\\n' \"$1\"\\n", encoding="utf-8")
            executable.chmod(0o700)
            digest = hashlib.sha256(executable.read_bytes()).hexdigest()
            cfg = {
                "allowed_root": str(root), "state_dir": str(base / "state"),
                "trusted_operations": {
                    "audit-helper": {
                        "executable": str(executable),
                        "executable_sha256": digest,
                        "permitted_roots": [str(root)],
                        "permitted_actions": ["inspect"],
                    }
                },
            }
            req = {
                "protocol": 1, "id": "safe-trusted-1", "cwd": str(root),
                "explanation": "Check only the registered helper",
                "trusted_operation": {"name": "audit-helper", "action": "inspect"},
                "operation_id": "safe-logical-1",
            }
            validated = validate_request(req, "safe-trusted-1.json", root, 60)
            plan = resolve_write_plan(validated, cfg)
            self.assertEqual(plan["effective"], "trusted_operation")
            self.assertEqual(plan["confirmation_category"], "trusted_operation")
            self.assertEqual(plan["trusted_operation_sha256"], digest)
            result = run_shell(root, "exit 77", b"", 10, argv_override=[str(executable), "inspect", str(root)])
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("SAFE:inspect", result["stdout_text"])
            self.assertFalse(result["filesystem_sandboxed"])

    def test_unknown_operation_is_denied_without_privileged_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp).resolve()
            req = {
                "protocol": 1, "id": "unknown-trusted", "cwd": str(root),
                "explanation": "This should be denied",
                "trusted_operation": {"name": "unknown", "action": "inspect"},
                "operation_id": "unknown-op",
            }
            validated = validate_request(req, "unknown-trusted.json", root, 60)
            with self.assertRaises(TrustedOperationPolicyError):
                resolve_write_plan(validated, {"allowed_root": str(root), "state_dir": str(root / "state")})

    def test_agent_cannot_mix_shell_stdin_or_unkeyed_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp).resolve()
            base = {
                "protocol": 1, "id": "reject-unsafe", "cwd": str(root),
                "explanation": "Reject untrusted capability escalation",
                "trusted_operation": {"name": "audit-helper", "action": "inspect"},
                "operation_id": "stable-op",
            }
            for extra in ({"command": "echo bad"}, {"write_scope": "system"},
                          {"stdin_b64": "YQ=="}, {"operation_id": None},
                          {"trusted_operation": {"name": "audit-helper", "action": "inspect", "executable": "/bin/sh"}}):
                req = dict(base, **extra)
                with self.subTest(extra=extra), self.assertRaises(ValueError):
                    validate_request(req, "reject-unsafe.json", root, 60)
