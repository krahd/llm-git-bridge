"""The v5->v6 cutover may never fork request journals, with or without workspaces."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

CUTOVER = Path(__file__).resolve().parents[1] / "cutover.sh"


class V5StateContinuityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = CUTOVER.read_text()
        match = re.search(
            r'SAME_STATE="\$\(python3 - "\$STATE_DIR" "\$LEGACY_STATE_DIR" <<\x27PYSTATE\x27\n(.*?)\nPYSTATE',
            cls.source, re.S,
        )
        if not match:
            raise AssertionError("expected durable v5 state identity gate")
        cls.checker = match.group(1)

    def check(self, home, candidate, original):
        env = os.environ.copy()
        env["HOME"] = str(home)
        return subprocess.run(
            [sys.executable, "-c", self.checker, str(candidate), str(original)],
            env=env, capture_output=True, text=True, timeout=5,
        )

    def test_same_journal_without_workspace_directory_is_required_and_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            root = home / ".local/state/production"
            root.mkdir(parents=True)
            self.assertFalse((root / "workspaces").exists())
            self.assertEqual(self.check(home, root, root).returncode, 0)

    def test_different_journal_rejected_even_without_workspace_directory(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            root = home / ".local/state/production"
            root.mkdir(parents=True)
            other = home / ".local/state/candidate"
            other.mkdir()
            self.assertNotEqual(self.check(home, other, root).returncode, 0)

    def test_symlinked_state_or_ancestor_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            root = home / ".local/state/production"
            root.mkdir(parents=True)
            link = home / ".local/state/linked"
            link.symlink_to(root, target_is_directory=True)
            self.assertNotEqual(self.check(home, link, link).returncode, 0)
            parent = home / "statealias"
            parent.symlink_to(root.parent, target_is_directory=True)
            child = parent / "production"
            self.assertNotEqual(self.check(home, child, child).returncode, 0)

    def test_path_alias_relative_and_outside_home_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            root = home / ".local/state/production"
            root.mkdir(parents=True)
            self.assertNotEqual(self.check(home, root / ".." / "production", root).returncode, 0)
            self.assertNotEqual(self.check(home, Path("relative"), Path("relative")).returncode, 0)
            outside = Path(td) / "unrelated"
            outside.mkdir()
            self.assertNotEqual(self.check(home, outside, outside).returncode, 0)

    def test_gate_occurs_before_first_source_stop_and_candidate_bootstrap(self):
        c = self.source
        gate = c.index('SAME_STATE="$(python3 - "$STATE_DIR" "$LEGACY_STATE_DIR"')
        old_stop = c.index("# Stop old consumers first.")
        candidate = c.index('launchctl bootstrap "gui/$' + '{UID_NOW}" "$NEW_PLIST"')
        self.assertLess(gate, old_stop)
        self.assertLess(old_stop, candidate)
        self.assertIn("v5 durable journal and workspace state identity differs", c)
        self.assertIn("stage v6 with STATE_DIR=", c)


if __name__ == "__main__":
    unittest.main()
