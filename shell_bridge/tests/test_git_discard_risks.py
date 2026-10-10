from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge


class GitDiscardRiskTests(unittest.TestCase):
    def test_discard_or_forced_delete_rejects_no_prompt_assumption(self):
        dangerous = (
            "git restore path/to/draft.txt",
            "git restore --source=HEAD file.py",
            "git restore --staged --worktree file.py",
            "git -C /tmp/repo checkout -- dirty.txt",
            "git checkout -f feature",
            "git checkout --force feature",
            "git switch --discard-changes feature",
            "git switch -f feature",
            "git rm -f modified-uncommitted.txt",
            "git rm --force modified-uncommitted.txt",
            "git stash drop",
            "git stash clear",
            "git branch -D unmerged-feature",
        )
        for command in dangerous:
            with self.subTest(command=command):
                self.assertEqual(
                    bridge.high_impact_command_category(command),
                    "git_destructive_local",
                )

    def test_index_only_and_informational_git_commands_do_not_prompt(self):
        benign = (
            "git status --short",
            "git diff",
            "git restore --staged file.py",
            "git restore --help",
            "git checkout main",
            "git switch feature",
            "git rm --cached file.py",
            "git stash list",
            "git stash show",
            "git branch -d merged-feature",
        )
        for command in benign:
            with self.subTest(command=command):
                self.assertIsNone(bridge.high_impact_command_category(command))

    def test_high_impact_discard_warning_also_applies_in_git_worktree(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir()
            original = bridge._git_repository_write_roots
            bridge._git_repository_write_roots = lambda cwd, allowed: [repo]
            try:
                plan = bridge.resolve_write_plan(
                    {"cwd": repo, "command": "git restore .", "write_scope": "repository"},
                    {"allowed_root": str(root), "state_dir": str(root / "state")},
                )
                self.assertEqual(plan["effective"], "repository")
                self.assertEqual(bridge.high_impact_command_category(
                    "git restore ."), "git_destructive_local")
            finally:
                bridge._git_repository_write_roots = original


if __name__ == "__main__":
    unittest.main()
