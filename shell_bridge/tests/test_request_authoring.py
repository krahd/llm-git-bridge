import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shell_bridge.request_authoring import build_request, lint_request, render_argv, request_policy, validate_argv


class RequestAuthoringTests(unittest.TestCase):
    def test_literal_argv_renders_existing_v5_command_schema(self):
        req = build_request(
            request_id="status-001",
            cwd="/Users/tom/tom-repos/example",
            explanation="Inspect status.",
            argv=["git", "status", "--short", "--branch"],
            write_scope="read_only",
            timeout_seconds=30,
        )
        self.assertNotIn("argv", req)
        self.assertEqual(req["command"], "git status --short --branch")
        self.assertEqual(lint_request(req)["errors"], [])
        self.assertEqual(request_policy(req), "legacy_command_ok")

    def test_literal_arguments_are_shell_quoted(self):
        self.assertEqual(render_argv(["/usr/bin/printf", "%s", "$HOME"]), "/usr/bin/printf %s '$HOME'")

    def test_rejects_inline_shell_and_interpreter_code(self):
        cases = [
            ["/bin/zsh", "-lc", "rm -rf generated"],
            ["bash", "-c", "git push --force origin main"],
            ["python3", "-c", "print('x')"],
            ["node", "-e", "console.log('x')"],
            ["env", "git", "status"],
        ]
        for argv in cases:
            with self.assertRaises(ValueError, msg=repr(argv)):
                validate_argv(argv)

    def test_shell_command_requires_explicit_opt_in(self):
        kwargs = dict(
            request_id="shell-001",
            cwd="/Users/tom/tom-repos/example",
            explanation="Exercise explicit shell opt-in.",
            command="git status --short",
        )
        with self.assertRaisesRegex(ValueError, "allow_shell"):
            build_request(**kwargs)
        req = build_request(**kwargs, allow_shell=True)
        self.assertEqual(req["command"], "git status --short")

    def test_compound_shell_warns_to_use_reviewed_helper(self):
        req = build_request(
            request_id="shell-002",
            cwd="/Users/tom/tom-repos/example",
            explanation="Exercise compound shell linting.",
            command="git status && git diff",
            allow_shell=True,
        )
        report = lint_request(req)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["policy"], "move_to_reviewed_helper")
        self.assertTrue(report["warnings"])

    def test_linter_rejects_unshipped_argv_request_schema(self):
        req = {
            "protocol": 1,
            "id": "argv-raw-001",
            "cwd": "/Users/tom/tom-repos/example",
            "argv": ["git", "status"],
            "explanation": "Do not emit this schema to production v5.",
        }
        report = lint_request(req)
        self.assertTrue(report["errors"])
        self.assertEqual(report["policy"], "invalid")

    def test_json_serializable(self):
        req = build_request(
            request_id="json-001",
            cwd="/Users/tom/tom-repos/example",
            explanation="Serialize request.",
            argv=["git", "status"],
        )
        json.dumps(req, sort_keys=True)


if __name__ == "__main__":
    unittest.main()
