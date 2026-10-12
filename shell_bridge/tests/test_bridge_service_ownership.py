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

    def test_shared_state_directory_alias_is_rejected(self):
        common = self.home / "durable" / "state"
        self.service("bridge-a", "one", state=str(common))
        self.service("bridge-b", "two", state=str(common.parent / "x" / ".." / "state"))
        report = self.check("bridge-a", "bridge-b")
        self.assertFalse(report["safe_to_stage"])
        self.assertTrue(any("shared state_dir" in p for p in report["problems"]))

    def test_relative_state_directory_is_rejected(self):
        self.service("bridge-a", "one", state="relative-state")
        report = self.check("bridge-a")
        self.assertFalse(report["safe_to_stage"])

    def test_loaded_pid_disappearing_between_launchctl_and_ps_holds(self):
        self.service("bridge-a", "production")
        listing = "PID\\tStatus\\tLabel\\n1786\\t0\\tbridge-a\\n"
        result = inspect_services(
            self.home, listing,
            "2000 /usr/bin/python3 /tmp/unrelated.py\\n",
        )
        self.assertFalse(result["safe_to_stage"])
        self.assertIn(
            "launchd PID missing from process inventory: bridge-a PID=1786",
            result["problems"],
        )
        self.assertFalse(result["mutations_performed"])

    def test_process_argv_secrets_not_reported(self):
        self.service("bridge-a", "production")
        listing = "PID\\tStatus\\tLabel\\n1786\\t0\\tbridge-a\\n"
        result = inspect_services(
            self.home, listing,
            "1786 /usr/bin/python3 /opt/bridge.py daemon --config /tmp/cfg "
            "--password=NEVER_LOG_THIS\\n",
        )
        self.assertTrue(result["safe_to_stage"])
        self.assertNotIn("NEVER_LOG_THIS", json.dumps(result))
        self.assertEqual(len(result["running_claimants"]), 1)

    def test_unmanaged_manual_daemon_is_rejected(self):
        self.service("bridge-a", "one")
        launchd = "PID\tStatus\tLabel\n101\t0\tbridge-a\n"
        processes = ("101 /usr/bin/python3 /opt/bridge.py daemon --config /a\n"
                     "333 /usr/bin/python3 /tmp/manual/bridge.py daemon --config /b\n")
        report = inspect_services(self.home, launchd, processes)
        self.assertFalse(report["safe_to_stage"])
        self.assertTrue(any("unmanaged bridge daemon PID=333" == p
                            for p in report["problems"]))

    def test_known_launchd_daemon_process_is_not_rejected(self):
        self.service("bridge-a", "one")
        report = inspect_services(
            self.home, "PID\tStatus\tLabel\n101\t0\tbridge-a\n",
            "101 /usr/bin/python3 /opt/bridge.py daemon --config /a\n",
        )
        self.assertTrue(report["safe_to_stage"])

    def test_unrelated_macos_bridge_named_daemon_is_ignored(self):
        listing = "PID\\tStatus\\tLabel\\n123\\t0\\tcom.apple.SystemBridge\\n"
        report = inspect_services(self.home, listing)
        self.assertTrue(report["safe_to_stage"])
        self.assertEqual(report["loaded"], [])

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

    def test_active_duplicate_consumers_are_reported_separately(self):
        a, b, dormant = "bridge-production-v6", "bridge-production-legacy", "bridge-old"
        for label in (a, b, dormant):
            self.service(label, "production")
        listing = ("PID\tStatus\tLabel\n"
                   f"1786\t0\t{a}\n"
                   f"1738\t0\t{b}\n"
                   f"-\t1\t{dormant}\n")
        result = inspect_services(self.home, listing)
        self.assertFalse(result["safe_to_stage"])
        self.assertEqual(result["schema"], 1)
        self.assertEqual(
            {r["pid"] for r in result["running_claimants"]},
            {"1786", "1738"})
        self.assertEqual(result["parked_loaded"], [dormant])
        self.assertTrue(any("active shared requests_folder_id" in p
                            for p in result["active_collisions"]))
        self.assertTrue(any("shared requests_folder_id" in p
                            for p in result["problems"]))
        self.assertEqual(len(result["loaded"]), 3)
        self.assertFalse(result["mutations_performed"])

    def test_only_parked_loaded_jobs_remain_a_conservative_hold(self):
        a, b = "bridge-old-one", "bridge-old-two"
        self.service(a, "same")
        self.service(b, "same")
        listing = f"PID\tStatus\tLabel\n-\t1\t{a}\n-\t1\t{b}\n"
        result = inspect_services(self.home, listing)
        self.assertFalse(result["safe_to_stage"])
        self.assertEqual(result["running_claimants"], [])
        self.assertEqual(result["active_collisions"], [])
        self.assertEqual(result["parked_loaded"], sorted((a, b)))
        self.assertTrue(any("shared drive_root_folder_id" in p
                            for p in result["problems"]))

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
