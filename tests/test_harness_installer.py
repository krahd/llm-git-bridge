from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "conversation_harness" / "install.sh"
README = ROOT / "conversation_harness" / "README.md"


class HarnessInstallerTests(unittest.TestCase):
    def test_runtime_namespace_is_distinct(self):
        text = INSTALLER.read_text()
        self.assertIn('APP_ID="chatgpt-conversation-harness-v1"', text)
        self.assertIn('LABEL="net.laurenzo.chatgpt-conversation-harness-v1"', text)
        self.assertIn("harness paths must not overlap the ChatGPT Shell Bridge runtime", text)

    def test_default_is_stage_only_and_activation_is_explicit(self):
        text = INSTALLER.read_text()
        self.assertIn("ACTIVATE=0", text)
        self.assertIn("--activate) ACTIVATE=1", text)
        self.assertIn('if [ "$ACTIVATE" -eq 0 ]', text)

    def test_activation_operates_only_on_harness_label(self):
        text = INSTALLER.read_text()
        self.assertIn('launchctl bootout "gui/${UID_NOW}/${LABEL}"', text)
        self.assertNotIn("LEGACY_LABEL", text)
        self.assertNotIn("io.llm-git-bridge", text)

    def test_installer_reconciles_launchd_and_retries_bootstrap_boundedly(self):
        text = INSTALLER.read_text()
        self.assertIn('launchctl print "gui/${UID_NOW}/${LABEL}"', text)
        self.assertIn('for _ in 1 2 3; do', text)
        self.assertIn('BOOTSTRAPPED=0', text)
        self.assertIn('socket_is_live()', text)
        self.assertIn('previous harness daemon still owns the Unix socket', text)
        self.assertIn('OLD_PID=', text)
        self.assertIn('kill -0 "$OLD_PID"', text)
        self.assertIn('rm -f "$STATE_DIR/harness.sock"', text)
        self.assertIn('refusing to remove unexpected non-socket harness.sock', text)
        self.assertIn('browser-status >/dev/null 2>&1', text)

    def test_installer_drops_stale_bytecode_and_hashes_safari_extension(self):
        text = INSTALLER.read_text()
        self.assertIn("-name '__pycache__'", text)
        self.assertIn("-name '*.pyc'", text)
        self.assertIn("'schema':2", text)
        self.assertIn("'safari_files':safari_files", text)

    def test_readme_documents_coexistence(self):
        text = README.read_text()
        self.assertIn("coexist indefinitely", text)
        self.assertIn("does not unload or restart any Shell Bridge label", text)

    def test_installer_stages_temporary_safari_extension(self):
        text = INSTALLER.read_text()
        self.assertIn('SAFARI_EXTENSION_DIR="$INSTALL_DIR/safari-extension"', text)
        self.assertIn('cp -R "$REPO_ROOT/safari/extension" "$SAFARI_EXTENSION_DIR"', text)
        self.assertIn('SAFARI_EXTENSION_DIR=$SAFARI_EXTENSION_DIR', text)

    def test_readme_documents_xcode_free_temporary_pilot(self):
        text = README.read_text()
        self.assertIn("Temporary Safari pilot (no Xcode required)", text)
        self.assertIn("Add Temporary Extension", text)
        self.assertIn("safari-extension/", text)


if __name__ == "__main__":
    unittest.main()
