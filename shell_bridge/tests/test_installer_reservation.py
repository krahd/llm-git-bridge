import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


INSTALL = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
MARKER = "<<'PYINSTALLLOCK'"
START = INSTALL.index("import os,stat,sys", INSTALL.index(MARKER))
END = INSTALL.index("PYINSTALLLOCK", START)
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
            # Exercise the installer's exact reservation command, repeatedly:
            # a non-atomic mkdir(1) produced two winners in ~1% of races.
            line = next(l for l in INSTALL.splitlines()
                        if l.startswith("(umask 077;") and "INSTALL_LOCK_DIR" in l)
            reservation = line.rsplit(")", 1)[0] + ")"
            reservation = reservation.replace('"$INSTALL_LOCK_DIR"', '"$1"')
            for attempt in range(60):
                lock = root.with_name(f"{root.name}.{attempt}")
                command = ["/bin/bash", "-c", reservation, "sh", str(lock)]
                first = subprocess.Popen(command, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE)
                second = subprocess.Popen(command, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE)
                p1 = first.communicate(timeout=10)
                p2 = second.communicate(timeout=10)
                self.assertEqual(sorted([first.returncode, second.returncode]),
                                 [0, 1], (attempt, p1, p2))
                self.assertTrue(lock.is_dir())
                self.assertEqual(lock.stat().st_mode & 0o077, 0)

    def test_reservation_does_not_depend_on_mkdir_utility_semantics(self):
        reservation = next(l for l in INSTALL.splitlines()
                           if l.startswith("(umask 077;") and "INSTALL_LOCK_DIR" in l)
        self.assertIn("os.mkdir(", reservation)
        self.assertNotIn("; mkdir ", reservation)

    def test_lock_is_acquired_before_remote_discovery_and_any_install_copy(self):
        self.assertLess(INSTALL.index('INSTALL_LOCK_HELD=1'),
                        INSTALL.index("\nchoose_allowed_root\n"))
        self.assertLess(INSTALL.index('INSTALL_LOCK_HELD=1'),
                        INSTALL.index('cp "$SCRIPT_DIR/bridge.py"'))
        self.assertIn("another installer is active or previous installation ended indeterminately",
                      INSTALL)
        self.assertIn('if [ "$INSTALL_LOCK_HELD" -eq 1 ]; then', INSTALL)
        self.assertIn('rmdir "$INSTALL_LOCK_DIR"', INSTALL)
        self.assertNotIn('rm -rf "$INSTALL_LOCK_DIR"', INSTALL)


if __name__ == "__main__":
    unittest.main()
