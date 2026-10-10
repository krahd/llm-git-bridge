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

    def test_same_mailbox_upgrade_requires_staged_cutover(self):
        self.assertIn("LEGACY_SHELL_CONFIG", self.text)
        self.assertIn("active v5 uses this same mailbox", self.text)
        self.assertIn("install.sh --stage-only", self.text)
        self.assertIn("cutover.sh out-of-band", self.text)


    def test_installer_builds_native_menu_bar_approval_app(self):
        self.assertIn('approval_gui.swift', self.text)
        self.assertIn('swiftc is required to build the native approval menu-bar app', self.text)
        self.assertIn("'CFBundleExecutable':'local-executor-approval'", self.text)
        self.assertIn("'ApprovalRoot':approval_root", self.text)
        self.assertIn("'ApprovalHelperPath':helper_path", self.text)
        self.assertIn("'ApprovalPythonPath':python_path", self.text)
        self.assertIn('"$INSTALL_DIR/approval_helper.py" "$PYTHON_BIN"', self.text)
        self.assertNotIn('exec "$PYTHON_BIN" "$INSTALL_DIR/approval_helper.py" --queue', self.text)

    def test_manifest_embedded_python_has_no_literal_newline_in_string(self):
        bad = "f.write(" + chr(39) + chr(10) + chr(39) + ")"
        self.assertNotIn(bad, self.text)
        self.assertIn("f.write(chr(10))", self.text)

    def test_stage_manifest_binds_no_auth_approval_and_generated_artifacts(self):
        for token in (
            "'approval_helper_sha256':sha256(approval_helper_path)",
            "'config_sha256':sha256(config_path)",
            "'launchagent_plist_sha256':sha256(plist_path)",
            "'approval_app_info_sha256':sha256(app_info_path)",
            "'approval_app_executable_sha256':sha256(app_exec_path)",
            "Contents/MacOS/local-executor-approval",
        ):
            self.assertIn(token, self.text)

    def test_candidate_config_uses_its_own_approval_app_and_purges_legacy_keys(self):
        self.assertIn('"$STATE_DIR" "$SHELL_BIN" "$INSTALL_DIR"', self.text)
        self.assertIn("state,shell,install_dir=sys.argv[1:]", self.text)
        self.assertIn("Path(install_dir)/'Local Executor Approval.app'", self.text)
        self.assertIn("cfg.pop('operator_approval_public_key', None)", self.text)
        self.assertIn("cfg.pop('operator_approval_public_key_sha256', None)", self.text)

if __name__ == "__main__":
    unittest.main()

    def test_mailbox_inspector_installed_and_registered_for_approval(self):
        self.assertIn('cp "$SCRIPT_DIR/bridge_mailbox_inspector.py"', self.text)
        self.assertIn("'bridge_mailbox_inspector_sha256':sha256(inspector_path)", self.text)
        self.assertIn("registered['bridge-mailbox-inspect'] = {", self.text)
        self.assertIn("'permitted_actions': ['inspect']", self.text)
        self.assertIn("'permitted_roots': [str(Path.home())]", self.text)
        self.assertNotIn("'requires_confirmation': False", self.text)
