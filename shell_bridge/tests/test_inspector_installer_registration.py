import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from trusted_operations import registered_operation_from_config

INSTALL = (ROOT / "install.sh").read_text()
START = INSTALL.index("<<'PY'\nimport hashlib,json,os,sys") + len("<<'PY'\n")
END = INSTALL.index("\nPY\nchmod 600", START)
CONFIG_SCRIPT = INSTALL[START:END]


class InspectorInstallerRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=pathlib.Path.cwd())
        self.addCleanup(self.tmp.cleanup)
        self.home = pathlib.Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.install = self.home / ".local/share/bridge-v6"
        self.install.mkdir(parents=True)
        self.inspector = self.install / "bridge_mailbox_inspector.py"
        self.inspector.write_text('#!/usr/bin/env python3\nprint("ok")\n')
        self.inspector.chmod(0o700)
        self.agent_root = self.home / "repos"
        self.agent_root.mkdir()
        self.cfg = self.home / ".config/bridge-v6/config.json"
        self.cfg.parent.mkdir(parents=True)
        self.args = [str(self.cfg), "drive:", "Candidate mailbox", "root-1",
                     "req-1", "res-1", "instance-1", str(self.agent_root),
                     str(self.home / ".local/state/bridge"), "/bin/zsh", str(self.install)]

    def run_config(self, enabled, ssh_policy=None):
        env = os.environ.copy()
        env.pop("REGISTER_PINNED_SSH_POLICY_DIR", None)
        if ssh_policy is not None:
            env["REGISTER_PINNED_SSH_POLICY_DIR"] = ssh_policy
        env.update(HOME=str(self.home),
                   REGISTER_MAILBOX_INSPECTOR="1" if enabled else "0")
        return subprocess.run([sys.executable, "-c", CONFIG_SCRIPT, *self.args],
                              text=True, capture_output=True, env=env, check=False)

    def test_explicit_registration_is_sha_pinned_and_approved(self):
        result = self.run_config(True)
        self.assertEqual(result.returncode, 0, result.stderr)
        cfg = json.loads(self.cfg.read_text())
        helper = registered_operation_from_config(
            cfg, "bridge-mailbox-inspect", "inspect", self.agent_root)
        self.assertEqual(helper.executable, self.inspector)
        self.assertTrue(helper.requires_confirmation)
        self.assertEqual(helper.permitted_actions, ("inspect",))

    def test_default_install_has_no_trusted_registry(self):
        result = self.run_config(False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("bridge-mailbox-inspect",
                         json.loads(self.cfg.read_text()).get("trusted_operations", {}))

    def test_opt_in_pinned_ssh_registers_only_fixed_actions(self):
        policy_dir = self.home / ".config" / "bridge-ssh-policy"
        policy_dir.mkdir(parents=True)
        policy_dir.chmod(0o700)
        identity = policy_dir / "private-key"
        identity.write_text("test fixture")
        identity.chmod(0o600)
        known = policy_dir / "known_hosts"
        known.write_text("example.invalid ssh-ed25519 FAKE_FIXTURE\\n")
        known.chmod(0o600)
        policy = policy_dir / "ssh-policy.json"
        policy.write_text(json.dumps({
            "schema": 1, "host": "example.invalid", "user": "deploy",
            "port": 22, "identity_file": str(identity),
        }))
        policy.chmod(0o600)
        executable = self.install / "ssh_pinned_helper.py"
        executable.write_text((ROOT / "ssh_pinned_helper.py").read_text())
        executable.chmod(0o700)
        result = self.run_config(False, ssh_policy=str(policy_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        cfg = json.loads(self.cfg.read_text())
        op = registered_operation_from_config(
            cfg, "ssh-pinned-readonly", "status", self.agent_root)
        self.assertEqual(op.executable, executable)
        self.assertEqual(op.permitted_roots, (policy_dir,))
        self.assertEqual(op.permitted_actions, ("status", "identity"))
        self.assertTrue(op.requires_confirmation)

    def test_installer_rejects_agent_writable_ssh_identity(self):
        policy_dir = self.home / ".config" / "ssh-pinned"
        policy_dir.mkdir(parents=True)
        policy_dir.chmod(0o700)
        identity = self.agent_root / "id-key"
        identity.write_text("fake secret")
        identity.chmod(0o600)
        known = policy_dir / "known_hosts"
        known.write_text("example.invalid ssh-ed25519 FAKE\\n")
        known.chmod(0o600)
        config = policy_dir / "ssh-policy.json"
        config.write_text(json.dumps({
            "schema": 1, "host": "example.invalid",
            "user": "deploy", "port": 22, "identity_file": str(identity),
        }))
        config.chmod(0o600)
        program = self.install / "ssh_pinned_helper.py"
        program.write_text((ROOT / "ssh_pinned_helper.py").read_text())
        program.chmod(0o700)
        result = self.run_config(False, ssh_policy=str(policy_dir))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("agent-writable SSH private identity", result.stderr)

    def test_installer_refuses_escapable_agent_writable_helper(self):
        self.agent_root = self.home
        self.args[7] = str(self.agent_root)
        result = self.run_config(True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("agent-writable inspector", result.stderr)


if __name__ == "__main__":
    unittest.main()
