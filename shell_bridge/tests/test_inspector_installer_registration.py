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
        self.tmp = tempfile.TemporaryDirectory()
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

    def run_config(self, enabled):
        env = os.environ.copy()
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

    def test_installer_refuses_escapable_agent_writable_helper(self):
        self.agent_root = self.home
        self.args[7] = str(self.agent_root)
        result = self.run_config(True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("agent-writable inspector", result.stderr)


if __name__ == "__main__":
    unittest.main()
