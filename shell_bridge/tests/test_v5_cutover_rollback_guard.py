"""Test that failed v5 shutdown never replays ambiguously completed work."""
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest

CUTOVER = Path(__file__).resolve().parents[1] / "cutover.sh"


class V5RestorationGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = CUTOVER.read_text()
        cls.rollback = cls.source.split("rollback() {", 1)[1].split(
            "trap rollback EXIT", 1)[0]
        cls.v5 = cls.rollback.split(
            'if [ "$CUTOVER_SOURCE_VERSION" = 5 ]; then', 1)[1].split(
            'if [ "$CUTOVER_SOURCE_VERSION" = 6 ]', 1)[0]
        match = re.search(r"<<'PYV5RESTORE'\n(.*?)\nPYV5RESTORE", cls.v5, re.S)
        if not match:
            raise AssertionError("missing v5 pre-admission owner verifier")
        cls.classifier = match.group(1)

    def check(self, records, safe=True, problems=None):
        inventory = {
            "safe_to_stage": safe,
            "problems": [] if problems is None else problems,
            "loaded": records,
        }
        return subprocess.run(
            [sys.executable, "-c", self.classifier, json.dumps(inventory),
             "prod", "io.llm-git-bridge.daemon", "com.tom.chatgpt-shell-bridge"],
            text=True, capture_output=True, timeout=5,
        )

    def test_no_remaining_source_job_is_a_safe_owner_inventory(self):
        self.assertEqual(self.check([]).returncode, 0)

    def test_original_owner_still_loaded_is_not_an_unknown_owner(self):
        owner = [{"label": "io.llm-git-bridge.daemon",
                  "drive_root_folder_id": "prod"}]
        self.assertEqual(self.check(owner).returncode, 0)

    def test_new_production_consumer_blocks_restoration(self):
        record = [{"label": "net.laurenzo.local-executor-bridge-v6-candidate",
                   "drive_root_folder_id": "prod"}]
        self.assertNotEqual(self.check(record).returncode, 0)

    def test_unsafe_or_unknown_inventory_blocks_restoration(self):
        self.assertNotEqual(self.check([], safe=False).returncode, 0)
        self.assertNotEqual(
            self.check([], problems=["loaded owner config unreadable"]).returncode, 0)

    def test_separate_staging_mailbox_is_not_misclassified(self):
        record = [{"label": "net.laurenzo.local-executor-bridge-v6-stage",
                   "drive_root_folder_id": "staging"}]
        self.assertEqual(self.check(record).returncode, 0)

    def test_pre_admission_checks_gate_all_v5_rebootstrap(self):
        b = self.rollback
        v = self.v5
        self.assertIn("V5_UNSAFE_TO_RESTORE=1", v)
        self.assertIn("V5_UNSAFE_TO_RESTORE=0", v)
        self.assertLess(v.index("verify_quiescent_journal.py"),
                        v.index("verify_pending_approvals.py"))
        self.assertLess(v.index("verify_pending_approvals.py"),
                        v.index('python3 "$OWNER_SCRIPT"'))
        restore = b.index('launchctl bootstrap "gui/${UID_NOW}" "$plist"')
        self.assertLess(b.index('V5_UNSAFE_TO_RESTORE" -eq 1'), restore)
        self.assertIn("pre_admission_source_restoration_hold", b)
        self.assertIn("cutover-reconciliation-required", b)
        self.assertIn("cutover-rollback-services.tsv", b)

    def test_bash_syntax_and_post_admission_never_restart_old(self):
        proc = subprocess.run(["bash", "-n", str(CUTOVER)],
                              text=True, capture_output=True, timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        candidate = self.rollback.split(
            'if [ "$STARTED_NEW" -eq 1 ]; then', 1)[1].split(
            'elif [ "$OLD_CONSUMERS_STOPPED"', 1)[0]
        self.assertNotIn('launchctl bootstrap "gui/${UID_NOW}" "$plist"', candidate)


if __name__ == "__main__":
    unittest.main()
