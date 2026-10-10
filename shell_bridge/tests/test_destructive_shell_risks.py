from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge


class DestructiveShellRiskTests(unittest.TestCase):
    def test_git_clean_and_reset_variants_are_high_impact(self):
        for command in (
            "git clean -fdx",
            "git clean -d -f",
            "git -C /tmp/repo clean -d -f",
            "git -C /tmp/repo clean --force -d",
            "git -C /tmp/repo reset --hard HEAD",
            "git -C /tmp/repo reset HEAD --hard",
            "git reset --hard",
        ):
            with self.subTest(command=command):
                self.assertEqual(bridge.high_impact_command_category(command),
                                 "git_destructive_local")

    def test_recursive_rm_variants_are_high_impact(self):
        for command in ("rm -rf something", "rm -r -f something",
                        "rm --recursive --force something",
                        "sudo rm -f -r directory"):
            with self.subTest(command=command):
                self.assertEqual(bridge.high_impact_command_category(command),
                                 "filesystem_recursive_delete")

    def test_benign_commands_remain_unprompted(self):
        for command in ("git status", "git diff --stat", "git clean -n",
                        "printf ok", "rm --help"):
            with self.subTest(command=command):
                self.assertIsNone(bridge.high_impact_command_category(command))

    def test_high_impact_gate_applies_independently_of_repository_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            cfg = {"allowed_root": str(root), "state_dir": str(root / "state")}
            original = bridge._git_repository_write_roots
            bridge._git_repository_write_roots = lambda cwd, allowed: [repo]
            try:
                command = "git -C repo clean -d -f"
                plan = bridge.resolve_write_plan(
                    {"cwd": repo, "command": command, "write_scope": "repository"},
                    cfg)
                self.assertEqual(plan["effective"], "repository")
                self.assertEqual(bridge.high_impact_command_category(command),
                                 "git_destructive_local")
            finally:
                bridge._git_repository_write_roots = original


if __name__ == "__main__":
    unittest.main()
