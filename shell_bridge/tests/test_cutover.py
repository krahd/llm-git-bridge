import pathlib
import unittest


class CutoverIdentityTests(unittest.TestCase):
    def test_cutover_targets_canonical_v6_identity_and_retires_old_services(self):
        text = (pathlib.Path(__file__).parents[1] / "cutover.sh").read_text()
        self.assertIn("net.laurenzo.local-executor-bridge", text)
        self.assertIn("LOCAL_EXECUTOR_BRIDGE_CUTOVER=1", text)
        self.assertIn("com.tom.chatgpt-shell-bridge", text)
        self.assertIn("io.llm-git-bridge.daemon", text)
        self.assertIn("net.laurenzo.mac-executor-bridge", text)
        self.assertNotIn("MAC_EXECUTOR_BRIDGE_LABEL", text)
        self.assertNotIn("MAC_EXECUTOR_BRIDGE_CUTOVER=1", text)


    def test_cutover_is_transactional_and_out_of_band(self):
        text = (pathlib.Path(__file__).parents[1] / "cutover.sh").read_text()
        self.assertIn("cutover must be run out-of-band", text)
        self.assertIn("active_requests", text)
        self.assertIn("production request mailbox is not empty", text)
        self.assertIn("LOCAL_EXECUTOR_BRIDGE_OK", text)
        self.assertIn("Cutover failed after stopping legacy consumers; restoring", text)
        self.assertIn("ln -sfn", text)
        smoke = text.index("LOCAL_EXECUTOR_BRIDGE_OK")
        compat = text.index("ln -sfn")
        self.assertGreater(compat, smoke)


    def test_cutover_verifies_staged_manifest_and_approval_helper_before_service_switch(self):
        text = (pathlib.Path(__file__).parents[1] / "cutover.sh").read_text()
        manifest = text.index('install-manifest.json')
        stop_old = text.index('# Stop old consumers first.')
        self.assertLess(manifest, stop_old)
        self.assertIn('approval_helper_sha256', text)
        self.assertIn('staged v6 install manifest integrity check failed', text)
        self.assertIn('EXPECTED_APPROVAL_APP="$NEW_INSTALL_DIR/Local Executor Approval.app"', text)
        self.assertIn('staged v6 approval app executable missing', text)
        self.assertLess(text.index('staged v6 approval app executable missing'), stop_old)

    def test_cutover_preflight_is_side_effect_free_and_requires_fresh_health(self):
        text = (pathlib.Path(__file__).parents[1] / "cutover.sh").read_text()
        self.assertIn('STARTED_NEW=0', text)
        self.assertIn('OLD_CONSUMERS_STOPPED=0', text)
        self.assertIn('if [ "$STARTED_NEW" -eq 1 ]', text)
        self.assertIn('if [ "$OLD_CONSUMERS_STOPPED" -eq 1 ]', text)
        self.assertIn('v6 service is already loaded; reconcile', text)
        self.assertIn('production health.json is stale', text)

    def test_cutover_proves_legacy_consumers_stopped_and_rollback_is_recoverable(self):
        text = (pathlib.Path(__file__).parents[1] / "cutover.sh").read_text()
        self.assertIn('loaded legacy service has no rollback plist', text)
        self.assertIn('failed to stop legacy service', text)
        self.assertIn('legacy service is still loaded after bootout', text)
        self.assertIn('OLD_CONSUMERS_STOPPED=1', text)
        stop = text.index('# Stop old consumers first.')
        start = text.index('launchctl bootstrap "gui/${UID_NOW}" "$NEW_PLIST"')
        self.assertLess(stop, start)
        self.assertLess(text.index('OLD_CONSUMERS_STOPPED=1', stop), start)

    def test_cutover_preserves_legacy_workspace_state_on_upgrade(self):
        text = (pathlib.Path(__file__).parents[1] / "cutover.sh").read_text()
        self.assertIn("LEGACY_STATE_DIR", text)
        self.assertIn("stage v6 with STATE_DIR=", text)

if __name__ == "__main__":
    unittest.main()
