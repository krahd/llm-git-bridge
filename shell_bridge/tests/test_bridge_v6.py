import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("bridge_v6", ROOT / "bridge.py")
bridge = importlib.util.module_from_spec(spec); spec.loader.exec_module(bridge)


class BridgeV6SecurityTests(unittest.TestCase):
    def test_installed_build_identity_is_reported_and_verified(self):
        import hashlib
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            content = {"bridge.py": b"test bridge", "workspace.py": b"test workspace",
                       "approval_helper.py": b"test approval",
                       "trusted_operations.py": b"test trusted module",
                       "bridge_mailbox_inspector.py": b"test inspector",
                       "Local Executor Approval.app/Contents/Info.plist": b"test app info",
                       "Local Executor Approval.app/Contents/MacOS/local-executor-approval": b"test app executable"}
            for name, raw in content.items():
                (home / name).parent.mkdir(parents=True, exist_ok=True)
                (home / name).write_bytes(raw)
            m = {"schema": 1, "source_commit": "1" * 40}
            for name, key in (("bridge.py", "bridge_sha256"),
                              ("workspace.py", "workspace_sha256"),
                              ("approval_helper.py", "approval_helper_sha256"),
                              ("trusted_operations.py", "trusted_operations_sha256"),
                              ("bridge_mailbox_inspector.py", "bridge_mailbox_inspector_sha256"),
                              ("Local Executor Approval.app/Contents/Info.plist", "approval_app_info_sha256"),
                              ("Local Executor Approval.app/Contents/MacOS/local-executor-approval", "approval_app_executable_sha256")):
                m[key] = hashlib.sha256(content[name]).hexdigest()
            (home / "install-manifest.json").write_text(json.dumps(m))
            original = bridge.__file__
            try:
                bridge.__file__ = str(home / "bridge.py")
                good = bridge.installed_build_identity()
                self.assertEqual(good["build_source_commit"], "1" * 40)
                self.assertEqual(good["build_code_integrity"], "matches_manifest")
                self.assertEqual(len(good["build_manifest_sha256"]), 64)
                (home / "approval_helper.py").write_text("modified helper")
                self.assertEqual(bridge.installed_build_identity()["build_code_integrity"], "mismatch")
                (home / "approval_helper.py").write_bytes(content["approval_helper.py"])
                (home / "Local Executor Approval.app/Contents/MacOS/local-executor-approval").write_bytes(b"tampered native app")
                self.assertEqual(bridge.installed_build_identity()["build_code_integrity"], "mismatch")
            finally:
                bridge.__file__ = original

    def test_version_and_product(self):
        self.assertEqual(bridge.VERSION, "6")
        self.assertEqual(bridge.PRODUCT_NAME, "Local Executor Bridge")

    def test_repository_shell_has_no_network_and_restricts_home_reads(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); repo = root / "repo"; repo.mkdir(); (repo / ".git").mkdir()
            # Stub repository root discovery because this test is about authority planning.
            old = bridge._git_repository_write_roots
            bridge._git_repository_write_roots = lambda cwd, allowed: [repo]
            try:
                plan = bridge.resolve_write_plan(
                    {"cwd": repo, "command": "printf ok", "write_scope": "repository"},
                    {"allowed_root": str(root), "state_dir": str(root / "state")},
                )
            finally:
                bridge._git_repository_write_roots = old
            self.assertFalse(plan["allow_network"])
            self.assertTrue(plan["deny_home_reads"])
            self.assertEqual(plan["read_roots"], [root.resolve()])

    def test_sandbox_profile_denies_network_and_home_read(self):
        p = bridge.sandbox_profile([Path("/tmp/repo")], allow_network=False,
                                   read_roots=[Path("/tmp/repo")], deny_home_reads=True)
        self.assertIn("(deny network*)", p)
        self.assertIn("(deny file-read*", p)
        home = Path.home().resolve()
        self.assertIn(str(home), p)
        self.assertIn(f'(literal "{home / ".gitconfig"}")', p)
        self.assertIn(f'(subpath "{home / ".config" / "git"}")', p)
        self.assertIn(f'(literal "{home / ".gitignore_global"}")', p)
        self.assertIn(f'(literal "{home.parent}")', p)
        self.assertIn(f'(literal "{home}")', p)
        # Read-only shell execution may write only to its own request scratch,
        # never every unrelated file below the macOS system temp directory.
        temp_root = Path(tempfile.gettempdir()).resolve()
        self.assertNotIn(f'(allow file-write* (subpath "{temp_root}")', p)
        self.assertNotIn(f'(subpath "{temp_root}")', p)

    def test_explicit_scratch_is_allowed_but_global_temp_is_not(self):
        with tempfile.TemporaryDirectory() as td:
            scratch = Path(td)
            profile = bridge.sandbox_profile([scratch], allow_network=False)
            self.assertIn(f'(subpath "{scratch.resolve()}")', profile)
            temp_root = Path(tempfile.gettempdir()).resolve()
            self.assertNotIn(f'(subpath "{temp_root}")', profile)

    def test_workspace_trust_rejects_outer_shell_operator(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); state = root / "state"; jobs = state / "workspaces/jobs"; jobs.mkdir(parents=True)
            repo = root / "repo"; repo.mkdir()
            job = "safe-job"
            (jobs / f"{job}.json").write_text(json.dumps({"repo": str(repo)}))
            ws = bridge.WORKSPACE_COORDINATOR
            cmd = f"python3 {ws} show --job {job}; touch /tmp/escape"
            self.assertFalse(bridge._trusted_workspace_coordinator(cmd, root, state))

    def test_child_environment_exports_isolated_cutover_controller_identity(self):
        env = bridge.child_environment(
            'request-1', caller_bridge_instance_id='staging-instance',
            caller_drive_root_folder_id='staging-root', caller_state_dir='/tmp/staging-state',
        )
        self.assertEqual(env['CHATGPT_SHELL_BRIDGE_REQUEST_ID'], 'request-1')
        self.assertEqual(env['LOCAL_EXECUTOR_CALLER_BRIDGE_INSTANCE_ID'], 'staging-instance')
        self.assertEqual(env['LOCAL_EXECUTOR_CALLER_DRIVE_ROOT_FOLDER_ID'], 'staging-root')
        self.assertEqual(env['LOCAL_EXECUTOR_CALLER_STATE_DIR'], '/tmp/staging-state')

    def test_safe_system_request_inside_repo_is_downgraded_without_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); repo = root / "repo"; repo.mkdir(); (repo / ".git").mkdir()
            old_roots = bridge._git_repository_write_roots
            bridge._git_repository_write_roots = lambda cwd, allowed: [repo]
            try:
                plan = bridge.resolve_write_plan(
                    {"cwd": repo, "command": "printf ok", "write_scope": "system"},
                    {"allowed_root": str(root), "state_dir": str(root / "state")},
                )
            finally:
                bridge._git_repository_write_roots = old_roots
            self.assertEqual(plan["effective"], "repository")
            self.assertTrue(plan["authority_downgraded"])
            self.assertNotIn("confirmation_category", plan)

    def test_safe_system_request_outside_repo_is_read_only_without_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = bridge.resolve_write_plan(
                {"cwd": root, "command": "printf ok", "write_scope": "system"},
                {"allowed_root": str(root), "state_dir": str(root / "state")},
            )
            self.assertEqual(plan["effective"], "read_only")
            self.assertTrue(plan["authority_downgraded"])
            self.assertNotIn("confirmation_category", plan)

    def test_outside_repo_mutation_still_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = bridge.resolve_write_plan(
                {"cwd": root, "command": "touch /tmp/outside-v6-test", "write_scope": "system"},
                {"allowed_root": str(root), "state_dir": str(root / "state")},
            )
            self.assertEqual(plan["effective"], "system")
            self.assertIn("confirmation_category", plan)

    def test_operator_approval_path_has_no_authentication_primitives(self):
        approval_sources = [ROOT / "approval_helper.py", ROOT / "approval_gui.swift", ROOT / "bridge.py"]
        forbidden = (
            "LocalAuthentication", "LAContext", "evaluatePolicy",
            "deviceOwnerAuthentication", "userPresence", "biometryType",
            "SecAccessControlCreateWithFlags", "kSecAccessControlUserPresence",
            "Touch ID",
        )
        combined = "\n".join(path.read_text(encoding="utf-8") for path in approval_sources)
        for primitive in forbidden:
            self.assertNotIn(primitive, combined, f"approval path must not authenticate via {primitive}")

    def test_approval_helper_exposes_persistent_menu_bar_queue(self):
        source = (ROOT / "approval_gui.swift").read_text(encoding="utf-8")
        self.assertIn("NSStatusBar.system.statusItem", source)
        self.assertIn("pendingRequests", source)
        self.assertIn("ApprovalRoot", source)
        self.assertIn("ApprovalHelperPath", source)
        self.assertIn("ApprovalPythonPath", source)
        self.assertNotIn('/usr/bin/python3', source)
        self.assertIn("terminationStatus", source)
        self.assertIn("lastDecisionError", source)
        self.assertIn("icon.isTemplate = true", source)
        self.assertIn("About Local Executor Bridge", source)
        self.assertIn("Allow once", source)
        self.assertIn("Reject", source)

    def test_approval_helper_status_item_identifier_is_bound(self):
        source = (ROOT / "approval_gui.swift").read_text(encoding="utf-8")
        self.assertIn('statusItem.button?.image = icon', source)

    def test_approval_helper_has_no_keyboard_or_focus_approval_path(self):
        source = (ROOT / "approval_gui.swift").read_text(encoding="utf-8")
        for primitive in ("performKeyEquivalent", "keyDown", "defaultButtonCell", "sendAction"):
            self.assertNotIn(primitive, source)
        self.assertNotIn("NSWindow(", source)
        self.assertEqual(source.count("runModal()"), 2)
        self.assertIn("reviewed[req.request_id] == req.payload_sha256", source)
        # Live GUI approvals must not rely on a hidden menu or blindly trust a summary.
        self.assertIn("NSApp.setActivationPolicy(.regular)", source)
        self.assertIn("DispatchQueue.main.async", source)
        self.assertIn("foregrounded[req.request_id] = req.payload_sha256", source)
        self.assertIn("Exact command (read the entire command before allowing):", source)
        self.assertIn('alert.addButton(withTitle: "Reject")', source)
        self.assertIn('alert.addButton(withTitle: "Review later")', source)
        self.assertIn('alert.addButton(withTitle: "Allow once")', source)
        self.assertIn("choice == .alertThirdButtonReturn", source)
        self.assertIn("$0.request_id == req.request_id && $0.payload_sha256 == req.payload_sha256", source)
        self.assertIn("NSApp.setActivationPolicy(.accessory)", source)
        self.assertIn("Exact command (read the entire command", source)

    def test_approval_helper_is_menu_only_and_never_auto_opens_pending_requests(self):
        source = (ROOT / "approval_gui.swift").read_text(encoding="utf-8")
        for primitive in ("LocalAuthentication", "LAContext", "SecureEnclave", "ApprovalWindowController"):
            self.assertNotIn(primitive, source)
        self.assertIn('#selector(allow(_:))', source)
        self.assertIn('#selector(reject(_:))', source)
        for label in ("What: ", "Why: ", "Where: ", "Allow once", "Reject"):
            self.assertIn(label, source)

class WakeLeaseTests(unittest.TestCase):
    def test_disabled_lease_is_inert(self):
        lease = bridge.WakeLease({"wake_lease_enabled": False, "wake_grace_seconds": 0})
        lease.acquire("test")
        self.assertEqual(lease.snapshot()["state"], "off")
        lease.close()


    def test_workspace_coordinator_is_bundled_with_bridge(self):
        self.assertEqual(bridge.WORKSPACE_COORDINATOR, (ROOT / "workspace.py").resolve())


class RepositoryVisibilityPolicyTests(unittest.TestCase):
    def test_public_repository_creation_is_prohibited(self):
        self.assertEqual(bridge.prohibited_command_reason("gh repo create example --public"), "public_repository_create")
        self.assertEqual(bridge.prohibited_command_reason("gh repo create example"), "public_repository_create")

    def test_private_repository_creation_is_not_prohibited(self):
        self.assertIsNone(bridge.prohibited_command_reason("gh repo create example --private"))

    def test_making_repository_public_is_prohibited(self):
        self.assertEqual(bridge.prohibited_command_reason("gh repo edit krahd/example --visibility public"), "public_repository_visibility")
        self.assertEqual(bridge.prohibited_command_reason("gh api -X PATCH repos/krahd/example -f visibility=public"), "public_repository_api")

    def test_normal_operations_on_existing_public_repo_are_not_prohibited(self):
        self.assertIsNone(bridge.prohibited_command_reason("git push origin main"))



class V6ApprovalHelperSourceTests(unittest.TestCase):
    def test_native_app_owns_status_item_instead_of_python_jxa_child(self):
        gui = (Path(__file__).resolve().parents[1] / "approval_gui.swift").read_text(encoding="utf-8")
        self.assertNotIn("osascript", gui)
        self.assertIn("NSApplication.shared", gui)
        self.assertIn("NSStatusBar.system.statusItem", gui)

if __name__ == "__main__": unittest.main()

class BridgeMenuIconAssetTests(unittest.TestCase):
    def test_menu_icon_is_bundled_as_a_template(self):
        from pathlib import Path
        r = Path(__file__).resolve().parents[1]
        self.assertTrue((r / "assets/bridge-menubar.png").is_file())
        swift = (r / "approval_gui.swift").read_text(encoding="utf-8")
        install = (r / "install.sh").read_text(encoding="utf-8")
        self.assertIn('icon.isTemplate = true', swift)
        self.assertIn('withExtension: "png"', swift)
        self.assertIn('Contents/Resources/bridge-menubar.png', install)
        self.assertNotIn('🌉', swift)
