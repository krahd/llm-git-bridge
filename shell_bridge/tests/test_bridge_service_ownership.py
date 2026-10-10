import json
import pathlib
import plistlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bridge_service_ownership import inspect_services


class LoadedBridgeOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = pathlib.Path(self.temp.name) / "home"
        self.home.mkdir()

    def service(self, label, root, req=None, state=None):
        directory = self.home / ".config" / label
        directory.mkdir(parents=True)
        config = directory / "config.json"
        config.write_text(json.dumps({
            "bridge_instance_id": label + "-instance",
            "drive_root_folder_id": root,
            "requests_folder_id": req or root + "-requests",
            "results_folder_id": root + "-results",
            "state_dir": state or "/state/" + label,
            "oauth_token": "NEVER_PRINT_THIS",
        }))
        target = self.home / "Library" / "LaunchAgents" / (label + ".plist")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as stream:
            plistlib.dump({"ProgramArguments": ["/opt/homebrew/bin/python3",
                                                  "/bin/bridge.py", "daemon",
                                                  "--config", str(config)]}, stream)

    def check(self, *labels):
        listing = "PID\tStatus\tLabel\n" + "".join(
            f"{100 + i}\t0\t{label}\n" for i, label in enumerate(labels))
        return inspect_services(self.home, listing)

    def test_distinct_dynamic_candidates_discovered(self):
        a = "io.llm-git-bridge.daemon"
        b = "net.laurenzo.local-executor-bridge-v6-candidate-abcdef012345"
        self.service(a, "production")
        self.service(b, "staging")
        report = self.check(a, b)
        self.assertEqual(len(report["loaded"]), 2)
        self.assertTrue(report["safe_to_stage"])
        self.assertNotIn("NEVER_PRINT_THIS", json.dumps(report))

    def test_shared_mailbox_or_folder_rejected(self):
        a, b = "bridge-a", "bridge-b"
        self.service(a, "same")
        self.service(b, "same")
        result = self.check(a, b)
        self.assertFalse(result["safe_to_stage"])
        self.assertTrue(any("shared drive_root_folder_id" in p
                            for p in result["problems"]))

    def test_shared_requests_without_shared_root_rejected(self):
        self.service("bridge-a", "one", req="same")
        self.service("bridge-b", "two", req="same")
        result = self.check("bridge-a", "bridge-b")
        self.assertFalse(result["safe_to_stage"])
        self.assertTrue(any("shared requests_folder_id" in p
                            for p in result["problems"]))

    def test_unknown_loaded_bridge_blocks(self):
        result = self.check("net.laurenzo.unexpected-bridge")
        self.assertFalse(result["safe_to_stage"])
        self.assertEqual(result["loaded"][0]["state"], "unverifiable")

    def test_loaded_without_pid_is_not_ignored(self):
        self.service("bridge-a", "one")
        report = inspect_services(
            self.home, "PID\tStatus\tLabel\n-\t0\tbridge-a\n")
        self.assertTrue(report["safe_to_stage"])
        self.assertIsNone(report["loaded"][0]["pid"])

    def test_unloaded_old_configs_do_not_generate_false_collisions(self):
        self.service("bridge-a", "same")
        self.service("bridge-b", "same")
        self.assertTrue(self.check("bridge-a")["safe_to_stage"])

    def test_symlinked_config_rejected(self):
        self.service("bridge-a", "one")
        config = self.home / ".config/bridge-a/config.json"
        other = self.home / "other.json"
        other.write_text(config.read_text())
        config.unlink()
        config.symlink_to(other)
        self.assertFalse(self.check("bridge-a")["safe_to_stage"])

    def test_missing_identity_is_not_reported_as_safe(self):
        self.service("bridge-a", "one")
        config = self.home / ".config/bridge-a/config.json"
        data = json.loads(config.read_text())
        data.pop("requests_folder_id")
        config.write_text(json.dumps(data))
        self.assertFalse(self.check("bridge-a")["safe_to_stage"])


if __name__ == "__main__":
    unittest.main()
