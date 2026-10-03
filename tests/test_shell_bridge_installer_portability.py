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



    def test_source_provenance_guard_precedes_mailbox_and_local_staging_side_effects(self):
        text = (ROOT / "shell_bridge" / "install.sh").read_text()
        guard = text.index('bridge source files are dirty; commit and audit the exact source before staging')
        self.assertLess(guard, text.index('choose_allowed_root'))
        self.assertLess(guard, text.index('choose_remote'))
        self.assertLess(guard, text.index('ensure_mailbox'))
        self.assertLess(guard, text.index('mkdir -p "$INSTALL_DIR"'))

    def test_generated_security_artifacts_are_bound_into_manifest(self):
        text = (ROOT / "shell_bridge" / "install.sh").read_text()
        for key in ('config_sha256','launchagent_plist_sha256','approval_app_info_sha256','approval_app_executable_sha256'):
            self.assertIn(key, text)
        self.assertLess(text.index('approval_app_executable_sha256'), text.index('if [ "$STAGE_ONLY" -eq 1 ]; then'))

    def test_stage_only_refuses_dirty_runtime_source_provenance(self):
        text = (ROOT / "shell_bridge" / "install.sh").read_text()
        guard = 'status --porcelain --untracked-files=no -- "${SOURCE_PATHS[@]}"'
        self.assertIn(guard, text)
        self.assertIn('fail "bridge source files are dirty; commit and audit the exact source before staging"', text)
        self.assertLess(text.index(guard), text.index('cp "$SCRIPT_DIR/bridge.py"'))

    def test_approval_app_config_uses_actual_install_dir_bundle(self):
        text = (ROOT / "shell_bridge" / "install.sh").read_text()
        self.assertIn('APP_BUNDLE="$INSTALL_DIR/Local Executor Approval.app"', text)
        self.assertIn('"$SHELL_BIN" "$APP_BUNDLE"', text)
        self.assertIn("'operator_approval_app':approval_app", text)
        self.assertNotIn("Path(state).parent.parent/'share'/'local-executor-bridge'", text)

    def test_stage_only_installs_and_hashes_approval_helper_without_starting_service(self):
        text = (ROOT / "shell_bridge" / "install.sh").read_text()
        self.assertIn('cp "$SCRIPT_DIR/approval_helper.py" "$INSTALL_DIR/approval_helper.py"', text)
        self.assertIn("'approval_helper_sha256':sha256(approval_helper_path)", text)
        stage = text.index('if [ "$STAGE_ONLY" -eq 1 ]; then')
        start = text.index('launchctl bootstrap', stage)
        self.assertLess(stage, start)
        stage_block = text[stage:start]
        self.assertIn('exit 0', stage_block)
        self.assertNotIn('launchctl bootstrap', stage_block)

    def test_native_approval_gui_is_compiled_as_persistent_menu_bar_app(self):
        installer = (ROOT / "shell_bridge" / "install.sh").read_text()
        bridge = (ROOT / "shell_bridge" / "bridge.py").read_text()
        swift = (ROOT / "shell_bridge" / "approval_gui.swift").read_text()
        self.assertIn("approval_gui.swift", installer)
        self.assertIn("command -v swiftc", installer)
        self.assertIn("local-executor-approval", installer)
        self.assertIn("'LSUIElement':True", installer)
        self.assertIn("'ApprovalRoot':approval_root", installer)
        self.assertNotIn('"/usr/bin/open", "-n"', bridge)
        self.assertIn("NSStatusBar.system.statusItem", swift)
        self.assertIn("Approve once", swift)
        self.assertIn("Reject", swift)
        self.assertIn("payload_sha256", swift)

    def test_native_approval_gui_rejects_unsafe_ids_and_uses_direct_toolchain(self):
        installer = (ROOT / "shell_bridge" / "install.sh").read_text()
        swift = (ROOT / "shell_bridge" / "approval_gui.swift").read_text()
        self.assertIn("validRequestID", swift)
        self.assertIn("validHex(r.nonce,count:64)", swift)
        self.assertIn("u.lastPathComponent==r.request_id", swift)
        self.assertIn("v.isSymbolicLink != true", swift)
        self.assertIn("q.isEnabled=rs.isEmpty", swift)
        self.assertIn("DispatchSource.makeFileSystemObjectSource", swift)
        self.assertIn("LAContext()", swift)
        self.assertIn("kSecAttrTokenIDSecureEnclave", swift)
        self.assertIn("XcodeDefault.xctoolchain/usr/bin/swiftc", installer)
        self.assertIn("Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk", installer)
        self.assertIn('"$SWIFTC_BIN" -sdk "$SWIFT_SDK"', installer)
        self.assertIn("CLANG_MODULE_CACHE_PATH", installer)
        self.assertIn("SWIFT_MODULECACHE_PATH", installer)

    def test_public_readme_documents_fresh_and_noninteractive_install(self):
        text = README.read_text()
        self.assertIn("## Fresh installation", text)
        self.assertIn('ALLOWED_ROOT="$HOME/repos"', text)
        self.assertIn("creates or adopts one verified Drive mailbox", text)

if __name__ == "__main__":
    unittest.main()
