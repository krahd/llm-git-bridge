"""v5-to-v6 must preserve exactly the same durable request journal."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from verify_v5_state_continuity import V5StateHold, verify

CUTOVER = ROOT / "cutover.sh"


class V5StateContinuityTests(unittest.TestCase):
    def test_same_journal_with_no_workspace_directory(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            source = home / ".local" / "state" / "production"
            source.mkdir(parents=True)
            self.assertFalse((source / "workspaces").exists())
            verify(source, source, home=home)

    def test_other_state_fails_even_without_workspaces(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            source = home / ".local" / "state" / "production"
            source.mkdir(parents=True)
            new = source.parent / "candidate"
            new.mkdir()
            with self.assertRaises(V5StateHold):
                verify(new, source, home=home)

    def test_symlinked_state_and_ancestor_fail(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            source = home / ".local" / "state" / "production"
            source.mkdir(parents=True)
            linked = source.parent / "linked"
            linked.symlink_to(source, target_is_directory=True)
            with self.assertRaises(V5StateHold):
                verify(linked, linked, home=home)
            parent = home / "state-alias"
            parent.symlink_to(source.parent, target_is_directory=True)
            with self.assertRaises(V5StateHold):
                verify(parent / source.name, parent / source.name, home=home)

    def test_relative_alias_and_external_state_fail(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            source = home / ".local" / "state" / "production"
            source.mkdir(parents=True)
            with self.assertRaises(V5StateHold):
                verify(Path("relative"), Path("relative"), home=home)
            with self.assertRaises(V5StateHold):
                verify(source / ".." / source.name, source, home=home)
            outside = Path(td) / "external"
            outside.mkdir()
            with self.assertRaises(V5StateHold):
                verify(outside, outside, home=home)

    def test_cli_and_shell_qualify_before_first_stop(self):
        source = CUTOVER.read_text()
        gate = source.index('python3 "$STATE_GATE" "$STATE_DIR" "$LEGACY_STATE_DIR"')
        stop = source.index("# Stop old consumers first.")
        start = source.index('launchctl bootstrap "gui/$' + '{UID_NOW}" "$NEW_PLIST"')
        self.assertLess(gate, stop)
        self.assertLess(stop, start)
        self.assertIn("stage v6 with STATE_DIR=", source)
        self.assertIn("verify_v5_state_continuity.py", source)
        syntax = subprocess.run(["bash", "-n", str(CUTOVER)],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)


if __name__ == "__main__":
    unittest.main()
