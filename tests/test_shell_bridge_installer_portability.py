from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "shell_bridge" / "install.sh"
README = ROOT / "shell_bridge" / "README.md"

class ShellBridgeInstallerPortabilityTests(unittest.TestCase):
    def test_installer_has_no_tomas_specific_default_root(self):
        text = INSTALLER.read_text()
        self.assertNotIn("$HOME/tom-repos", text)
        self.assertIn('ALLOWED_ROOT="${ALLOWED_ROOT:-}"', text)
        self.assertIn("ALLOWED_ROOT is required for non-interactive installation", text)

    def test_fresh_install_can_bootstrap_mailbox(self):
        text = INSTALLER.read_text()
        self.assertIn('rclone mkdir "${REMOTE}${BASE_PATH}"', text)
        self.assertIn('bridge-instance.json', text)
        self.assertIn("shell_bridge_instance", text)
        self.assertIn("refusing to adopt it implicitly", text)

    def test_multiple_remotes_require_explicit_or_interactive_choice(self):
        text = INSTALLER.read_text()
        self.assertIn("set RCLONE_REMOTE for non-interactive installation", text)
        self.assertIn("Choose the rclone remote", text)

    def test_public_readme_documents_fresh_and_noninteractive_install(self):
        text = README.read_text()
        self.assertIn("## Fresh installation", text)
        self.assertIn('ALLOWED_ROOT="$HOME/repos"', text)
        self.assertIn("creates or adopts one verified Drive mailbox", text)

if __name__ == "__main__":
    unittest.main()
