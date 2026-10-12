import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


INSTALL = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
MARKER = "<<'PYINSTALLLOCK'\n"
START = INSTALL.index(MARKER) + len(MARKER)
END = INSTALL.index("PYINSTALLLOCK\n", START)
OWNER_GATE = INSTALL[START:END]


class InstallerReservationTests(unittest.TestCase):
    def test_shell_syntax(self):
        subprocess.run(["/bin/bash", "-n",
                        str(Path(__file__).resolve().parents[1] / "install.sh")],
                       check=True)

    def test_owner_gate_accepts_private_directory(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / "state"
            directory.mkdir(mode=0o700)
            result = subprocess.run([sys.executable, "-c", OWNER_GATE, str(directory)],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_owner_gate_refuses_group_writable_directory_and_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / "state"
            directory.mkdir()
            directory.chmod(0o770)
            result = subprocess.run([sys.executable, "-c", OWNER_GATE, str(directory)],
                                    capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            directory.chmod(0o700)
            link = Path(td) / "alias"
            link.symlink_to(directory)
            result = subprocess.run([sys.executable, "-c", OWNER_GATE, str(link)],
                                    capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)

    def test_two_simultaneous_atomic_reservations_have_one_winner(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "bridge-installer-active.lock"
            command = ["/bin/bash", "-c",
                       '(umask 077; mkdir "$1")', "sh", str(root)]
            first = subprocess.Popen(command, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
            second = subprocess.Popen(command, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE)
            p1 = first.communicate(timeout=5)
            p2 = second.communicate(timeout=5)
            self.assertEqual(sorted([first.returncode, second.returncode]),
                             [0, 1], (p1, p2))
            self.assertTrue(root.is_dir())
            self.assertEqual(root.stat().st_mode & 0o077, 0)

    def test_lock_is_acquired_before_remote_discovery_and_any_install_copy(self):
        self.assertLess(INSTALL.index('INSTALL_LOCK_HELD=1'),
                        INSTALL.index("choose_allowed_root\nchoose_remote"))
        self.assertLess(INSTALL.index('INSTALL_LOCK_HELD=1'),
                        INSTALL.index('cp "$SCRIPT_DIR/bridge.py"'))
        self.assertIn("another installer is active or previous installation ended indeterminately",
                      INSTALL)
        self.assertIn('if [ "$INSTALL_LOCK_HELD" -eq 1 ]; then', INSTALL)
        self.assertIn('rmdir "$INSTALL_LOCK_DIR"', INSTALL)
        self.assertNotIn('rm -rf "$INSTALL_LOCK_DIR"', INSTALL)


if __name__ == "__main__":
    unittest.main()
