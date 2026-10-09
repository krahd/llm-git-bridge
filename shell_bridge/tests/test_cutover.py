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


    def test_cutover_is_transactional_and_allows_only_isolated_controller(self):
        text = (pathlib.Path(__file__).parents[1] / "cutover.sh").read_text()
        self.assertIn("LOCAL_EXECUTOR_CALLER_BRIDGE_INSTANCE_ID", text)
        self.assertIn("LOCAL_EXECUTOR_CALLER_DRIVE_ROOT_FOLDER_ID", text)
        self.assertIn("LOCAL_EXECUTOR_CALLER_STATE_DIR", text)
        self.assertIn("cutover controller is attached to the target production mailbox", text)
        self.assertIn("cutover controller is the target production bridge instance", text)
        self.assertNotIn("cutover must be run out-of-band", text)
        self.assertIn("active_requests", text)
        self.assertIn("pending_approvals", text)
        self.assertIn("started_without_finished", text)
        self.assertIn("production request mailbox is not empty", text)
        self.assertIn("LOCAL_EXECUTOR_BRIDGE_OK", text)
        self.assertIn("Cutover failed after stopping legacy consumers; restoring", text)
        smoke = text.index("v6 production smoke failed")
        committed = text.index("CUTOVER_COMMITTED=1", smoke)
        compat = text.index("ln -sfn", committed)
        archive = text.index("# Archive obsolete service plists", committed)
        self.assertLess(smoke, committed)
        self.assertLess(committed, compat)
        self.assertLess(committed, archive)


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

    def test_cutover_binds_generated_artifacts_and_supports_source_commit_pin(self):
        text = (pathlib.Path(__file__).parents[1] / "cutover.sh").read_text()
        for key in ('config_sha256','launchagent_plist_sha256','approval_app_info_sha256','approval_app_executable_sha256'):
            self.assertIn(key, text)
        self.assertIn('EXPECTED_SOURCE_COMMIT is required for production cutover', text)
        self.assertIn('does not match EXPECTED_SOURCE_COMMIT', text)

    def test_partial_legacy_stop_is_rollback_safe_and_drain_is_rechecked(self):
        text = (pathlib.Path(__file__).parents[1] / "cutover.sh").read_text()
        bootout = text.index('launchctl bootout "gui/${UID_NOW}/${label}" || fail')
        stopped = text.index('OLD_CONSUMERS_STOPPED=1', bootout)
        second_check = text.index('PENDING_AFTER_STOP=', stopped)
        new_start = text.index('launchctl bootstrap "gui/${UID_NOW}" "$NEW_PLIST"', second_check)
        self.assertLess(bootout, stopped)
        self.assertLess(stopped, second_check)
        self.assertLess(second_check, new_start)
        self.assertIn('if ! launchctl print "gui/${UID_NOW}/${label}"', text)

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
