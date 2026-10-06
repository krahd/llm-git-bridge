import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bridge_v6", ROOT / "bridge.py")
bridge = importlib.util.module_from_spec(spec); spec.loader.exec_module(bridge)


class BridgeV6SecurityTests(unittest.TestCase):
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
        self.assertIn(str(Path.home().resolve()), p)

    def test_workspace_trust_rejects_outer_shell_operator(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); state = root / "state"; jobs = state / "workspaces/jobs"; jobs.mkdir(parents=True)
            repo = root / "repo"; repo.mkdir()
            job = "safe-job"
            (jobs / f"{job}.json").write_text(json.dumps({"repo": str(repo)}))
            ws = bridge.WORKSPACE_COORDINATOR
            cmd = f"python3 {ws} show --job {job}; touch /tmp/escape"
            self.assertFalse(bridge._trusted_workspace_coordinator(cmd, root, state))

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
        approval_sources = [ROOT / "approval_helper.py", ROOT / "bridge.py"]
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
        source = (ROOT / "approval_helper.py").read_text(encoding="utf-8")
        self.assertIn("NSStatusBar", source)
        self.assertIn("pendingRecords", source)
        self.assertIn("objectAtIndex(i)", source)
        self.assertIn("--queue", source)
        self.assertIn("--decide", source)
        self.assertIn('imageWithSystemSymbolNameAccessibilityDescription("bridge"', source)
        self.assertIn("🌉", source)
        self.assertIn("About Local Executor Bridge", source)
        self.assertIn('"Version: " + versionLabel', source)
        self.assertIn('"Build: " + buildId', source)
        self.assertIn("install-manifest.json", source)
        self.assertNotIn('button.title = "LEB"', source)


    def test_approval_helper_is_menu_only_and_never_auto_opens_pending_requests(self):
        source = (ROOT / "approval_helper.py").read_text(encoding="utf-8")
        self.assertNotIn("review(firstNew)", source)
        self.assertNotIn("function review(req)", source)
        self.assertNotIn('\"review:\"', source)
        self.assertIn('\"allow:\"', source)
        self.assertIn('\"reject:\"', source)
        self.assertIn("Allow once", source)
        self.assertIn("Reject", source)

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
    def test_jxa_registered_delegate_is_resolved_from_objc_namespace(self):
        source = (Path(__file__).resolve().parents[1] / "approval_helper.py").read_text(encoding="utf-8")
        self.assertIn('ObjC.registerSubclass({', source)
        self.assertIn('const Delegate = $.LEBApprovalQueueDelegate;', source)
        self.assertNotIn('const Delegate = ObjC.registerSubclass({', source)

if __name__ == "__main__": unittest.main()
