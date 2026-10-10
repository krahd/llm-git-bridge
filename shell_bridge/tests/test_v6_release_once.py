from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "v6_release_once.sh").read_text()
INSTALL = (ROOT / "install.sh").read_text()
CUTOVER = (ROOT / "cutover.sh").read_text()


class SingleRunReleaseContractTests(unittest.TestCase):
    def test_single_entrypoint_progresses_past_isolated_staging(self):
        self.assertIn("prepare_v6_on_mac.sh", SCRIPT)
        self.assertIn("approval_canary", SCRIPT)
        self.assertIn("install.sh --stage-only", SCRIPT)
        self.assertIn("bash shell_bridge/cutover.sh", SCRIPT)
        self.assertIn("V6_PRODUCTION_PROVISIONAL_AND_APPROVAL_VERIFIED=1", SCRIPT)

    def test_real_approval_denial_and_allow_before_cutover(self):
        deny = SCRIPT.index('approval_canary "$CANDIDATE_ROOT" "$CANDIDATE_ALLOWED" deny staging')
        allow = SCRIPT.index('approval_canary "$CANDIDATE_ROOT" "$CANDIDATE_ALLOWED" allow staging')
        stage_production = SCRIPT.index('echo "Phase 3: stage immutable production')
        cutover = SCRIPT.index('bash shell_bridge/cutover.sh')
        self.assertLess(deny, allow)
        self.assertLess(allow, stage_production)
        self.assertLess(stage_production, cutover)
        self.assertIn('MIGRATE LIMITED SSH', SCRIPT)
        self.assertIn('Arbitrary SSH, SCP, SFTP and RSYNC are NOT yet qualified', SCRIPT)

    def test_dangerous_actions_are_guarded(self):
        self.assertIn('[ -z "${CHATGPT_SHELL_BRIDGE_REQUEST_ID:-}" ]', SCRIPT)
        self.assertIn('SOURCE="$(git rev-parse HEAD)"', SCRIPT)
        self.assertIn("refs/remotes/origin/main", SCRIPT)
        self.assertIn('] || fail "this worktree is not the freshly fetched canonical', SCRIPT)
        self.assertIn('if ! rclone --drive-root-folder-id "$root"', SCRIPT)
        self.assertIn('HOLD: ambiguous approval request upload', SCRIPT)
        self.assertIn('RETIRE_OLD_AFTER_SMOKE=0', SCRIPT)
        self.assertNotIn("git reset --hard", SCRIPT)
        self.assertNotIn("git clean -", SCRIPT)
        self.assertNotIn("launchctl bootout", SCRIPT)

    def test_retirement_occurs_only_after_production_approval(self):
        approval = SCRIPT.index('approval_canary "$PROD_ROOT" "$PROD_ALLOWED" allow production')
        archive = SCRIPT.index('echo "Phase 8: archive stopped production v5')
        self.assertLess(approval, archive)
        self.assertLess(SCRIPT.index("PYEXCLUSIVE"), archive)
        self.assertIn("PRODUCTION_MAILBOX_EXCLUSIVE_OWNER_VERIFIED=1", SCRIPT)
        self.assertIn('cutover-rollback-services.tsv', SCRIPT)
        self.assertIn('legacy archive postcondition failed', SCRIPT)
        self.assertIn('recovery_fault_injection_live_verified":False', SCRIPT)
        self.assertIn('legacy_state_preserved":True', SCRIPT)

    def test_ssh_requires_operator_policy_and_live_dual_smoke(self):
        self.assertIn('V6_SSH_POLICY_DIR', SCRIPT)
        self.assertIn('REGISTER_PINNED_SSH_POLICY_DIR="$V6_SSH_POLICY_DIR"', SCRIPT)
        stage = SCRIPT.index('ssh_status_canary "$CANDIDATE_ROOT" "$CANDIDATE_ALLOWED" staging')
        cutover = SCRIPT.index('bash shell_bridge/cutover.sh')
        prod = SCRIPT.index('ssh_status_canary "$PROD_ROOT" "$PROD_ALLOWED" production')
        retirement = SCRIPT.index('echo "Phase 8: archive stopped production v5')
        self.assertLess(stage, cutover)
        self.assertLess(cutover, prod)
        self.assertLess(prod, retirement)
        self.assertIn("verify_v6_pinned_ssh.py", SCRIPT)
        self.assertIn("setup_v6_pinned_ssh.py", SCRIPT)
        self.assertIn("SSH enrollment incomplete; production v5 has not been modified", SCRIPT)
        self.assertIn("ambiguous SSH smoke submission", SCRIPT)

    def test_register_read_only_inspector_only_opt_in(self):
        self.assertIn('REGISTER_MAILBOX_INSPECTOR=1', SCRIPT)
        self.assertIn('if os.environ.get("REGISTER_MAILBOX_INSPECTOR") == "1":', INSTALL)
        self.assertIn('"permitted_actions":["inspect"]', INSTALL)
        self.assertIn('refusing agent-writable inspector registration', INSTALL)

    def test_cutover_uses_live_owner_inventory_before_stopping_v5(self):
        self.assertIn("bridge_service_ownership.py", CUTOVER)
        self.assertIn("KNOWN_CONSUMERS", CUTOVER)
        self.assertLess(CUTOVER.index('KNOWN_CONSUMERS='), CUTOVER.index('# Stop old consumers first.'))
        self.assertIn('no verified production consumers to transfer', CUTOVER)
        self.assertIn('net.laurenzo.local-executor-bridge', CUTOVER)


if __name__ == "__main__":
    unittest.main()
