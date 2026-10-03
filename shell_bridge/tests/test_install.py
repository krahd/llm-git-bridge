import pathlib
import unittest


class InstallScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (pathlib.Path(__file__).parents[1] / "install.sh").read_text()

    def test_v6_product_and_launchagent_identity_are_distinct(self):
        self.assertIn('APP_NAME="local-executor-bridge"', self.text)
        self.assertIn('LOCAL_EXECUTOR_LABEL:-net.laurenzo.local-executor-bridge', self.text)
        self.assertIn('BASE_PATH="${BASE_PATH:-Local Executor Bridge}"', self.text)

    def test_old_services_are_retired_only_after_new_smoke_test(self):
        self.assertIn('OLD_LABEL_1="com.tom.chatgpt-shell-bridge"', self.text)
        self.assertIn('OLD_LABEL_2="io.llm-git-bridge.daemon"', self.text)
        self.assertIn('--retire-old-after-smoke', self.text)
        smoke = self.text.index("SHELL_BRIDGE_INSTALLED=1")
        retire = self.text.index('if [ "$RETIRE_OLD_AFTER_SMOKE" -eq 1 ]; then')
        self.assertGreater(retire, smoke)

    def test_launchagent_runs_daemon(self):
        self.assertIn("'ProgramArguments':[python,bridge,'daemon','--config',config]", self.text)

    def test_wake_lease_defaults_are_written(self):
        self.assertIn("'wake_lease_enabled'", self.text)
        self.assertIn("'wake_grace_seconds'", self.text)

    def test_installer_warns_when_private_oauth_client_is_missing_without_printing_secrets(self):
        self.assertIn('rclone config redacted "${REMOTE%:}"', self.text)
        self.assertIn("no private OAuth client_id", self.text)
        self.assertNotIn("config show", self.text)

    def test_installer_writes_runtime_provenance_manifest(self):
        self.assertIn("install-manifest.json", self.text)
        self.assertIn("'source_commit':source_commit", self.text)
        self.assertIn("'bridge_sha256':sha256(bridge_path)", self.text)
        self.assertIn("'workspace_sha256':sha256(workspace_path)", self.text)


    def test_launch_agent_propagates_canonical_state_dir(self):
        self.assertIn("LOCAL_EXECUTOR_BRIDGE_STATE_DIR", self.text)
        self.assertIn("CHATGPT_SHELL_BRIDGE_STATE_DIR", self.text)

if __name__ == "__main__":
    unittest.main()
