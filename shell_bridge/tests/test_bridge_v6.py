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

    def test_approval_wait_is_nonblocking_pending_state(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); app=root/'Approval.app'; app.mkdir()
            old=bridge._launch_operator_approval_helper
            bridge._launch_operator_approval_helper=lambda app_path:(True, '')
            try:
                cfg={'state_dir':str(root/'state'),'bridge_instance_id':'inst','operator_confirmation_mode':'dialog','operator_confirmation_timeout_seconds':60,'operator_approval_app':str(app)}
                r=bridge.request_operator_confirmation(request_id='req-1',cwd=root,command='true',category='system_write',cfg=cfg,explanation='test',requested_write_scope='system',effective_write_scope={'write_scope':'system'})
            finally: bridge._launch_operator_approval_helper=old
            self.assertEqual(r['state'],'pending'); self.assertFalse(r['approved'])
            pending=json.loads((root/'state'/'approvals'/'pending'/'req-1.json').read_text())
            self.assertEqual(pending['bridge_instance_id'],'inst'); self.assertEqual(len(pending['nonce']),64)

    def test_approval_exact_binding_changes_with_command(self):
        a=bridge._approval_projection(request_id='r',bridge_instance_id='i',nonce='a'*64,cwd=Path('/tmp'),command='one',category='system_write',explanation='x',requested_write_scope='system',effective_write_scope={'write_scope':'system'})
        b=dict(a); b['command']='two'
        self.assertNotEqual(bridge._approval_payload_hash(a),bridge._approval_payload_hash(b))

    def test_approval_projection_golden_vector_and_authority_summary(self):
        projection=bridge._approval_projection(
            request_id='golden-req', bridge_instance_id='golden-instance', nonce='ab'*32,
            cwd=Path('/tmp/repo'), command='printf ok', category='system_write',
            explanation='Golden approval vector', requested_write_scope='system',
            effective_write_scope={'allow_network':True,'effective':'system','requested':'system'},
        )
        self.assertTrue(projection['network_authority'])
        self.assertEqual(projection['authority_summary']['requested_write_scope'],'system')
        self.assertEqual(projection['effect_summary'],{'category':'system_write','cwd':'/tmp/repo'})
        self.assertEqual(bridge._approval_payload_hash(projection),'9765cc265f2b6723127625d664f7f8849454a4f223a471e786d55ce2fd432506')

    def test_allow_rejects_non_mac_gui_client_before_key_lookup(self):
        ok,message=bridge._verify_approval_signature({'decision':'allow','client':'other','signature_algorithm':'ecdsa-p256-sha256'}, {}, {})
        self.assertFalse(ok)
        self.assertIn('client',message)

    def test_allow_rejects_unapproved_signature_algorithm_before_key_lookup(self):
        ok,message=bridge._verify_approval_signature({'decision':'allow','client':'mac_gui','signature_algorithm':'wrong'}, {}, {})
        self.assertFalse(ok)
        self.assertIn('signature algorithm',message)

    def test_child_environment_propagates_caller_identity(self):
        env=bridge.child_environment(
            'request-1', sandboxed=True, caller_bridge_instance_id='staging-instance',
            caller_drive_root_folder_id='staging-root', caller_state_dir='/tmp/staging-state',
        )
        self.assertEqual(env['CHATGPT_SHELL_BRIDGE_REQUEST_ID'],'request-1')
        self.assertEqual(env['LOCAL_EXECUTOR_CALLER_BRIDGE_INSTANCE_ID'],'staging-instance')
        self.assertEqual(env['LOCAL_EXECUTOR_CALLER_DRIVE_ROOT_FOLDER_ID'],'staging-root')
        self.assertEqual(env['LOCAL_EXECUTOR_CALLER_STATE_DIR'],'/tmp/staging-state')

    def test_system_scope_still_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = bridge.resolve_write_plan(
                {"cwd": root, "command": "true", "write_scope": "system"},
                {"allowed_root": str(root), "state_dir": str(root / "state")},
            )
            self.assertEqual(plan["confirmation_category"], "system_write")


class WakeLeaseTests(unittest.TestCase):
    def test_disabled_lease_is_inert(self):
        lease = bridge.WakeLease({"wake_lease_enabled": False, "wake_grace_seconds": 0})
        lease.acquire("test")
        self.assertEqual(lease.snapshot()["state"], "off")
        lease.close()


    def test_workspace_coordinator_is_bundled_with_bridge(self):
        self.assertEqual(bridge.WORKSPACE_COORDINATOR, (ROOT / "workspace.py").resolve())

if __name__ == "__main__": unittest.main()
