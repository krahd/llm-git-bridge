import json
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_v6_login_claimants import RestartRegistrationHold, inspect_agents


class FutureLoginOwnerTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.home = Path(t.name) / "home"
        self.agents = self.home / "Library" / "LaunchAgents"
        self.agents.mkdir(parents=True)
        self.cfgdir = self.home / ".config" / "production"
        self.cfgdir.mkdir(parents=True)
        self.cfg = self.cfgdir / "config.json"
        self.cfg.write_text(json.dumps({"drive_root_folder_id": "PROD"}))
        self.cfg.chmod(0o600)
        self.label = "net.laurenzo.local-executor-bridge"
        self.plist(self.label, self.cfg)

    def plist(self, label, config):
        target = self.agents / (label + ".plist")
        target.write_bytes(plistlib.dumps({
            "Label": label,
            "ProgramArguments": ["/usr/bin/python3", "/install/bridge.py",
                                 "daemon", "--config", str(config)],
            "RunAtLoad": True, "KeepAlive": True,
        }))
        return target

    def test_only_known_source_registration_can_start_after_login(self):
        report = inspect_agents(self.agents, self.label, "PROD")
        self.assertEqual(report["registered_production_labels"], [self.label])
        self.assertTrue(report["eligible_for_source_archive"])
        self.assertFalse(report["login_recovery_verified"])
        self.assertFalse(report["mutations_performed"])

    def test_parked_alias_with_same_mailbox_blocks(self):
        self.plist("io.llm-git-bridge.daemon", self.cfg)
        with self.assertRaises(RestartRegistrationHold):
            inspect_agents(self.agents, self.label, "PROD")

    def test_unknown_label_running_bridge_is_not_ignored(self):
        self.plist("com.example.bypass", self.cfg)
        with self.assertRaises(RestartRegistrationHold):
            inspect_agents(self.agents, self.label, "PROD")

    def test_different_staging_mailbox_may_remain_registered(self):
        cfgdir = self.home / ".config" / "staging"
        cfgdir.mkdir()
        cfg = cfgdir / "config.json"
        cfg.write_text(json.dumps({"drive_root_folder_id": "STAGING"}))
        cfg.chmod(0o600)
        self.plist("net.laurenzo.local-executor-bridge-v6-staging", cfg)
        report = inspect_agents(self.agents, self.label, "PROD")
        self.assertTrue(report["eligible_for_source_archive"])

    def test_agent_writable_config_is_not_a_future_login_identity(self):
        self.cfg.chmod(0o666)
        with self.assertRaises(RestartRegistrationHold):
            inspect_agents(self.agents, self.label, "PROD")

    def test_untrusted_bridge_config_outside_private_config_holds(self):
        outside = self.home / "tom-repos" / "public-config.json"
        outside.parent.mkdir()
        outside.write_text(json.dumps({"drive_root_folder_id": "STAGING"}))
        source = self.agents / (self.label + ".plist")
        source.unlink()
        self.plist(self.label, outside)
        with self.assertRaises(RestartRegistrationHold):
            inspect_agents(self.agents, self.label, "PROD")

    def test_symlinked_intermediate_config_directory_holds(self):
        link_parent = self.home / ".config" / "linked"
        link_parent.symlink_to(self.cfgdir, target_is_directory=True)
        source = self.agents / (self.label + ".plist")
        source.unlink()
        self.plist(self.label, link_parent / "config.json")
        with self.assertRaises(RestartRegistrationHold):
            inspect_agents(self.agents, self.label, "PROD")

    def test_agent_writable_plist_registration_is_not_trusted(self):
        source = self.agents / (self.label + ".plist")
        source.chmod(0o666)
        with self.assertRaises(RestartRegistrationHold):
            inspect_agents(self.agents, self.label, "PROD")

    def test_world_writable_launchagents_directory_is_not_trusted(self):
        self.agents.chmod(0o777)
        with self.assertRaises(RestartRegistrationHold):
            inspect_agents(self.agents, self.label, "PROD")

    def test_public_readable_config_is_not_trusted(self):
        self.cfg.chmod(0o644)
        with self.assertRaises(RestartRegistrationHold):
            inspect_agents(self.agents, self.label, "PROD")

    def test_broken_bridge_config_is_not_assumed_safe(self):
        self.cfg.unlink()
        with self.assertRaises(RestartRegistrationHold):
            inspect_agents(self.agents, self.label, "PROD")

    def test_linked_bridge_plist_blocks(self):
        target = self.agents / (self.label + ".plist")
        data = target.read_bytes()
        target.unlink()
        external = self.home / "other.plist"
        external.write_bytes(data)
        target.symlink_to(external)
        with self.assertRaises(RestartRegistrationHold):
            inspect_agents(self.agents, self.label, "PROD")

    def test_different_source_label_does_not_pass(self):
        with self.assertRaises(RestartRegistrationHold):
            inspect_agents(self.agents, "io.llm-git-bridge.daemon", "PROD")

    def test_native_approval_launchagent_is_not_a_mailbox_daemon(self):
        approval = self.agents / "net.laurenzo.local-executor-approval.123.plist"
        approval.write_bytes(plistlib.dumps({
            "Label": "net.laurenzo.local-executor-approval.123",
            "ProgramArguments": ["/usr/bin/python3", "/opt/approval_helper.py"],
        }))
        self.assertTrue(inspect_agents(self.agents, self.label, "PROD")
                        ["eligible_for_source_archive"])

    def test_unrelated_no_config_plist_does_not_block(self):
        other = self.agents / "com.apple.unrelated.plist"
        other.write_bytes(plistlib.dumps({"Label": "com.apple.unrelated",
                                           "ProgramArguments": ["/usr/bin/true"]}))
        self.assertTrue(inspect_agents(self.agents, self.label, "PROD")
                        ["eligible_for_source_archive"])


if __name__ == "__main__":
    unittest.main()
