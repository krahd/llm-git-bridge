import json
import tempfile
import unittest
from pathlib import Path

from shell_bridge import bridge
from shell_bridge.request_authoring import (
    MAX_ARGV_ITEMS,
    build_request,
    lint_request,
    request_policy,
    shell_complexity_reasons,
)


class RequestAuthoringTests(unittest.TestCase):
    def test_build_argv_request(self):
        req = build_request(
            request_id="safe-argv-001",
            cwd="/tmp/example",
            explanation="Inspect repository status.",
            argv=["git", "status", "--short"],
            write_scope="read_only",
        )
        self.assertEqual(req["argv"], ["git", "status", "--short"])
        self.assertNotIn("command", req)
        self.assertEqual(request_policy(req), "preferred_argv")

    def test_shell_requires_explicit_opt_in(self):
        with self.assertRaisesRegex(ValueError, "allow_shell=True"):
            build_request(
                request_id="shell-001",
                cwd="/tmp/example",
                explanation="Run a shell command.",
                command="git status --short",
            )

    def test_uppercase_request_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid request id"):
            build_request(
                request_id="Unsafe-ID",
                cwd="/tmp/example",
                explanation="Reject a non-canonical ID.",
                argv=["true"],
            )

    def test_timeout_ceiling_matches_bridge(self):
        with self.assertRaisesRegex(ValueError, "1..300"):
            build_request(
                request_id="timeout-001",
                cwd="/tmp/example",
                explanation="Reject an oversized timeout.",
                argv=["true"],
                timeout_seconds=301,
            )

    def test_argv_item_ceiling_matches_bridge(self):
        with self.assertRaisesRegex(ValueError, "too many items"):
            build_request(
                request_id="argv-count-001",
                cwd="/tmp/example",
                explanation="Reject oversized argv.",
                argv=["printf"] + ["x"] * MAX_ARGV_ITEMS,
            )

    def test_simple_shell_is_flagged_for_argv(self):
        report = lint_request({
            "protocol": 1,
            "id": "simple-shell-001",
            "cwd": "/tmp/example",
            "command": "git status --short",
            "explanation": "Inspect repository status.",
        })
        self.assertEqual(report["policy"], "rewrite_as_argv")
        self.assertTrue(any("prefer argv" in warning for warning in report["warnings"]))

    def test_compound_shell_is_flagged_for_helper(self):
        command = "git status --short && git diff --check | head > /tmp/out"
        reasons = shell_complexity_reasons(command)
        self.assertIn("command_chain", reasons)
        self.assertIn("pipeline", reasons)
        self.assertIn("redirection", reasons)
        report = lint_request({
            "protocol": 1,
            "id": "compound-shell-001",
            "cwd": "/tmp/example",
            "command": command,
            "explanation": "Inspect and validate repository changes.",
        })
        self.assertEqual(report["policy"], "move_to_reviewed_helper")

    def test_bridge_accepts_authored_argv_without_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = build_request(
                request_id="argv-bridge-001",
                cwd=tmp,
                explanation="Print a literal value.",
                argv=["printf", "%s", "hello world"],
                write_scope="read_only",
            )
            validated = bridge.validate_request(
                req,
                "argv-bridge-001.json",
                Path(tmp),
                max_timeout=300,
            )
        self.assertEqual(validated["execution_mode"], "argv")
        self.assertEqual(validated["argv"], ["printf", "%s", "hello world"])
        self.assertEqual(validated["command"], "printf %s 'hello world'")

    def test_rendered_argv_keeps_high_impact_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = build_request(
                request_id="argv-high-impact-001",
                cwd=tmp,
                explanation="Exercise classification only.",
                argv=["git", "push", "--force", "origin", "main"],
                write_scope="repository",
            )
            validated = bridge.validate_request(
                req,
                "argv-high-impact-001.json",
                Path(tmp),
                max_timeout=300,
            )
        self.assertIsNotNone(bridge.high_impact_command_category(validated["command"]))

    def test_invalid_base64_is_reported(self):
        report = lint_request({
            "protocol": 1,
            "id": "stdin-001",
            "cwd": "/tmp/example",
            "argv": ["cat"],
            "stdin_b64": "not base64!",
            "explanation": "Exercise stdin validation.",
        })
        self.assertIn("stdin_b64 is not valid base64", report["errors"])

    def test_cli_lint_shape_is_json_serializable(self):
        report = lint_request({
            "protocol": 1,
            "id": "argv-json-001",
            "cwd": "/tmp/example",
            "argv": ["git", "status"],
            "explanation": "Inspect status.",
        })
        json.dumps(report)
        self.assertEqual(report["errors"], [])


if __name__ == "__main__":
    unittest.main()
