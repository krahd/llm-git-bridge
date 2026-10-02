import subprocess
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class InstallerPackageTests(unittest.TestCase):
    def test_installer_bash_syntax_and_no_old_service_identity(self):
        text=(ROOT/"install.sh").read_text()
        self.assertNotIn("com.tom.chatgpt-shell-bridge",text)
        self.assertNotIn("io.llm-git-bridge.daemon",text)
        cp=subprocess.run(["bash","-n",str(ROOT/"install.sh")],capture_output=True,text=True)
        self.assertEqual(cp.returncode,0,cp.stderr)

    def test_package_excludes_python_cache(self):
        text=(ROOT/"install.sh").read_text()
        self.assertIn('cp "$SCRIPT_DIR/bridge.py" "$INSTALL_DIR/bridge.py"', text)
        self.assertIn('cp "$SCRIPT_DIR/workspace.py" "$INSTALL_DIR/workspace.py"', text)
        self.assertNotIn('cp -R "$SCRIPT_DIR"', text)
        self.assertNotIn('__pycache__', text)

if __name__=='__main__': unittest.main(verbosity=2)
