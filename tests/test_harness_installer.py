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

    def test_readme_documents_coexistence(self):
        text = README.read_text()
        self.assertIn("coexist indefinitely", text)
        self.assertIn("does not unload or restart any Shell Bridge label", text)


if __name__ == "__main__":
    unittest.main()
