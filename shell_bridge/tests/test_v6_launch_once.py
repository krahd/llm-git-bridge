import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LAUNCH = ROOT / "v6_launch_once.sh"


def git(*args, cwd=None):
    return subprocess.check_output(
        ["git", *args], cwd=cwd, text=True, stderr=subprocess.DEVNULL,
    ).strip()


class OneRunDirtyCheckoutLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.home = base / "home"
        self.home.mkdir()
        self.original = base / "original"
        self.original.mkdir()
        git("init", "-q", self.original.as_posix())
        git("checkout", "-qb", "main", cwd=self.original)
        (self.original / "shell_bridge").mkdir()
        (self.original / "shell_bridge" / "v6_release_once.sh").write_text(
            "#!/bin/bash\nexit 99\n", encoding="utf-8")
        git("add", ".", cwd=self.original)
        git("-c", "user.email=test@example.invalid",
            "-c", "user.name=Release Test", "commit", "-qm", "safe release fixture",
            cwd=self.original)
        self.origin = base / "canonical.git"
        git("clone", "-q", "--bare", str(self.original), str(self.origin))
        git("remote", "add", "origin", str(self.origin), cwd=self.original)
        self.original_sha = git("rev-parse", "HEAD", cwd=self.original)
        self.local_dirty_file = self.original / "approval_gui.swift"
        self.local_dirty_file.write_text("unpublished local work\n")
        self.env = os.environ.copy()
        self.env.update(HOME=str(self.home), V6_RELEASE_DRY_RUN="1")
        self.env.pop("CHATGPT_SHELL_BRIDGE_REQUEST_ID", None)

    def run_launcher(self):
        return subprocess.run(
            ["/bin/bash", str(LAUNCH), str(self.original)],
            env=self.env, text=True, capture_output=True, timeout=30,
        )

    def test_dirty_worktree_preserved_and_release_source_is_detached(self):
        before = git("status", "--porcelain", cwd=self.original)
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("V6_RELEASE_SOURCE_PREPARED_ONLY=1", result.stdout)
        self.assertEqual(git("status", "--porcelain", cwd=self.original), before)
        self.assertTrue(self.local_dirty_file.exists())
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.original),
                         self.original_sha)
        release = (self.home / ".local" / "state" /
                   "bridge-v6-release-sources" / self.original_sha)
        self.assertEqual(git("rev-parse", "HEAD", cwd=release),
                         self.original_sha)
        self.assertEqual(git("status", "--porcelain", cwd=release), "")
        self.assertEqual(git("rev-parse", "--abbrev-ref", "HEAD", cwd=release), "HEAD")

    def test_existing_release_worktree_is_not_replayed_or_overwritten(self):
        first = self.run_launcher()
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_launcher()
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("already exists; reconcile rather than rerun", second.stderr)
        self.assertTrue(self.local_dirty_file.exists())

    def test_no_production_release_is_invoked_in_safe_dry_run(self):
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("SHELL_BRIDGE_STAGED=1", result.stdout)
        self.assertNotIn("V6_PRODUCTION_ACCEPTED", result.stdout)

    def test_incorrect_origin_refused_before_mutation(self):
        env = dict(self.env)
        env.pop("V6_RELEASE_DRY_RUN")
        result = subprocess.run(
            ["/bin/bash", str(LAUNCH), str(self.original)],
            env=env, text=True, capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not the expected canonical GitHub", result.stderr)
        self.assertFalse((self.home / ".local" / "state").exists())


if __name__ == "__main__":
    unittest.main()
