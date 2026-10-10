from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / 'prepare_v6_on_mac.sh').read_text()

class LocalPreparationContractTests(unittest.TestCase):
    def test_never_runs_cutover_or_bootout(self):
        self.assertNotIn('launchctl bootout', SCRIPT)
        self.assertNotIn('bash shell_bridge/cutover.sh --', SCRIPT)
        self.assertNotIn('--retire-old-after-smoke', SCRIPT)
        self.assertIn('install.sh --stage-only', SCRIPT)

    def test_preserves_dirty_and_preexisting_installations(self):
        self.assertIn('git status --porcelain', SCRIPT)
        self.assertIn('if [ -e "$config" ] || [ -L "$config" ]', SCRIPT)
        self.assertIn('launchctl print', SCRIPT)

    def test_local_regression_and_mailbox_ownership_gates(self):
        self.assertIn("python3 -m unittest discover", SCRIPT)
        self.assertIn('bash -n "$script"', SCRIPT)
        self.assertIn("shell_bridge/cutover.sh", SCRIPT)
        self.assertIn("bridge_mailbox_inspector.py", SCRIPT)
        self.assertIn("shared_drive_roots", SCRIPT)
        self.assertIn("RCLONE_REMOTE", SCRIPT)
        self.assertIn("never adopt an unknown consumer mailbox", SCRIPT)
        self.assertIn("verify_v6_candidate_smoke.py", SCRIPT)
        self.assertIn("read_only", SCRIPT)
        self.assertIn("verify_v6_candidate_health.py", SCRIPT)
        self.assertIn("ISOLATED_V6_RUNNING=1", SCRIPT)
        self.assertIn('install.sh --stage-only', SCRIPT)
        self.assertIn('--drive-root-folder-id "$root_id"', SCRIPT)

if __name__ == '__main__':
    unittest.main()
