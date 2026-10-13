import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ProductionUpgradeStagingGuardTests(unittest.TestCase):
    def test_shell_syntax(self):
        for name in ("install.sh", "stage_v6_upgrade.sh"):
            with self.subTest(name=name):
                result = subprocess.run(
                    ["bash", "-n", str(ROOT / name)],
                    capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_staging_flags_enforce_no_launch_or_live_journal_writes(self):
        text = (ROOT / "install.sh").read_text()
        self.assertIn('--upgrade-stage-only) STAGE_ONLY=1; UPGRADE_STAGE=1', text)
        self.assertIn('if [ "$UPGRADE_STAGE" -ne 1 ]; then', text)
        self.assertIn('  "$' + '{R[@]}" mkdir "$' + '{REMOTE}requests"', text)
        self.assertIn('  "$' + '{R[@]}" mkdir "$' + '{REMOTE}results"', text)
        self.assertIn('mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" "$HOME/Library/LaunchAgents"', text)
        self.assertIn('chmod 700 "$INSTALL_DIR" "$CONFIG_DIR"', text)
        self.assertIn('else\n  mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" "$STATE_DIR"', text)
        self.assertIn('verify_upgrade_stage_source.py', text)
        self.assertLess(text.index('if [ "$STAGE_ONLY" -eq 1 ]; then\n  echo'),
                        text.index('launchctl bootstrap "gui/'))
        self.assertIn("os.environ['SOURCE_CONFIG_FILE'] if os.environ.get('UPGRADE_STAGE')=='1' else path", text)

    def test_staging_wrapper_has_fixed_source_and_no_service_actions(self):
        text = (ROOT / "stage_v6_upgrade.sh").read_text()
        self.assertIn("CHATGPT_SHELL_BRIDGE_REQUEST_ID", text)
        self.assertIn("EXPECTED_SOURCE_COMMIT", text)
        self.assertIn('git -C "$ROOT" diff --quiet HEAD', text)
        self.assertIn('install.sh" --upgrade-stage-only', text)
        self.assertNotIn("launchctl bootout", text)
        self.assertNotIn("launchctl bootstrap", text)
        self.assertNotIn("rclone copyto", text)


if __name__ == "__main__":
    unittest.main()
