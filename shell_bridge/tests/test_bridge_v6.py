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
