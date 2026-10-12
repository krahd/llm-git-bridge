from pathlib import Path
import re
import subprocess
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "audit_runtime_processes.sh"


class RuntimeProcessInventoryTests(unittest.TestCase):
    def test_script_syntax_is_valid(self):
        subprocess.run(["/bin/bash", "-n", str(SCRIPT)], check=True)

    def test_inventory_is_bounded_and_read_only(self):
        text = SCRIPT.read_text()
        for expected in (
            "launchctl print", "launchctl list", "ps -axo pid=,ppid=,comm=",
            "bridge_service_ownership.py", "inventory_only_no_mutation=1",
            "bridge_process_inventory_v2", "known_approval_apps:",
            "bridge_lock_observer.py", "LOCK_DESCRIPTOR_EVIDENCE_INCONCLUSIVE=1",
        ):
            self.assertIn(expected, text)
        for prohibited in (
            "launchctl bootout", "launchctl bootstrap", "launchctl kickstart",
            "kill -", "rm -", "git reset", "rclone delete", "os.remove(",
        ):
            self.assertNotIn(prohibited, text)
        self.assertIn("if (count++ < 40)", text)
        self.assertNotIn("ps -axww", text)

    def test_dynamic_v6_launchagent_filter_matches_actual_labels(self):
        source = SCRIPT.read_text()
        match = re.search(r"launchctl list \| awk '([^']+)'", source)
        self.assertIsNotNone(match, "dynamic label matcher missing")
        sample = (
            "PID Status Label\n"
            "1790 0 net.laurenzo.local-executor-bridge-v6-manual-bootstrap\n"
            "1768 0 net.laurenzo.local-executor-bridge-v6-candidate-0b84b2f-g2\n"
            "1400 0 com.apple.unrelated\n"
        )
        result = subprocess.run(["awk", match.group(1)], input=sample,
                                text=True, capture_output=True, check=True)
        self.assertIn("1790 net.laurenzo.local-executor-bridge-v6-manual-bootstrap",
                      result.stdout)
        self.assertIn("1768 net.laurenzo.local-executor-bridge-v6-candidate-0b84b2f-g2",
                      result.stdout)
        self.assertNotIn("unrelated", result.stdout)

    def test_live_release_hold_can_reference_this_inventory(self):
        release = (SCRIPT.parent / "v6_release_once.sh").read_text()
        self.assertIn("Read-only live LaunchAgent/mailbox inventory", release)
        self.assertIn("audit_runtime_processes.sh", release)


if __name__ == "__main__":
    unittest.main()
