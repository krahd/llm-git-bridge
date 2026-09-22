import pathlib
import unittest


class InstallScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (pathlib.Path(__file__).parents[1] / "install.sh").read_text()

    def test_canonical_launchagent_label_is_default(self):
        self.assertIn("SHELL_BRIDGE_LABEL:-io.llm-git-bridge.daemon", self.text)

    def test_launchagent_runs_shell_bridge_daemon_not_watch(self):
        self.assertIn("'ProgramArguments':[python,bridge,'daemon','--config',config]", self.text)
        self.assertNotIn("[python,bridge,'watch'", self.text)

    def test_installer_retires_known_legacy_launchagent(self):
        self.assertIn("io.llm-git-bridge.chatgpt-shell-bridge", self.text)
        self.assertIn("LEGACY_PLIST", self.text)
        self.assertIn("launchctl bootout", self.text)
        self.assertIn("legacy-launchagent", self.text)

    def test_installer_writes_runtime_provenance_manifest(self):
        self.assertIn("install-manifest.json", self.text)
        self.assertIn("'source_commit':source_commit", self.text)
        self.assertIn("'bridge_sha256':sha256(bridge_path)", self.text)
        self.assertIn("'workspace_sha256':sha256(workspace_path)", self.text)


if __name__ == "__main__":
    unittest.main()
