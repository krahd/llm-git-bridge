import subprocess
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
INSTALL=ROOT/"install.sh"

class InstallScriptTests(unittest.TestCase):
    def test_v6_identity_is_default(self):
        text=INSTALL.read_text()
        self.assertIn('APP_NAME="mac-executor-bridge"',text)
        self.assertIn('MAC_EXECUTOR_BRIDGE_LABEL:-net.laurenzo.mac-executor-bridge',text)
        self.assertIn('BASE_PATH="${BASE_PATH:-Mac Executor Bridge}"',text)

    def test_installer_does_not_retire_v5(self):
        text=INSTALL.read_text()
        self.assertNotIn('io.llm-git-bridge.daemon',text)
        self.assertNotIn('com.tom.chatgpt-shell-bridge',text)
        self.assertIn('cutover.sh',text)

    def test_cutover_names_both_old_service_labels(self):
        text=(ROOT/"cutover.sh").read_text()
        self.assertIn("com.tom.chatgpt-shell-bridge",text)
        self.assertIn("io.llm-git-bridge.daemon",text)
        self.assertIn("MAC_EXECUTOR_BRIDGE_CUTOVER=1",text)

    def test_shell_syntax(self):
        for script in [INSTALL,ROOT/"cutover.sh"]:
            cp=subprocess.run(["bash","-n",str(script)],capture_output=True,text=True)
            self.assertEqual(cp.returncode,0,cp.stderr)

if __name__=='__main__': unittest.main(verbosity=2)
