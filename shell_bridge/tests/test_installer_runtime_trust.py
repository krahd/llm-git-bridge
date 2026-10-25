"""The installed daemon must never run an interpreter an agent can replace."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

INSTALL = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
START = INSTALL.index("import os,sys", INSTALL.index("<<'PYTRUSTEDBIN'"))
GUARD = INSTALL[START:INSTALL.index("PYTRUSTEDBIN", START)]


def guard(binary, root):
    return subprocess.run([sys.executable, "-c", GUARD, str(binary), str(root)],
                          capture_output=True, text=True, timeout=10).returncode


class InstallerRuntimeTrustTests(unittest.TestCase):
    def test_interpreter_inside_allowed_root_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repos"
            venv = root / "project" / ".venv" / "bin"
            venv.mkdir(parents=True)
            (venv / "python3").write_text("")
            self.assertEqual(guard(venv / "python3", root), 1)

    def test_symlink_inside_allowed_root_to_system_python_is_refused(self):
        # An agent can retarget the link even though its target is trusted.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repos"
            venv = root / ".venv" / "bin"
            venv.mkdir(parents=True)
            os.symlink(sys.executable, venv / "python3")
            self.assertEqual(guard(venv / "python3", root), 1)

    def test_system_binary_outside_allowed_root_is_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repos"
            root.mkdir()
            self.assertEqual(guard(sys.executable, root), 0)

    def test_guard_runs_before_any_remote_or_local_install_write(self):
        guard_at = INSTALL.index("<<'PYTRUSTEDBIN'")
        self.assertLess(INSTALL.index("\nchoose_allowed_root\n"), guard_at)
        for later in ("\nensure_mailbox\n", 'cp "$SCRIPT_DIR/bridge.py"',
                      "PATH_VALUE=", "ApprovalPythonPath"):
            self.assertLess(guard_at, INSTALL.index(later), later)
        self.assertIn('for runtime_bin in "$PYTHON_BIN" "$RCLONE_BIN"; do', INSTALL)


if __name__ == "__main__":
    unittest.main()
