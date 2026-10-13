from pathlib import Path
import json
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "v6_release_once.sh").read_text()
INSTALL = (ROOT / "install.sh").read_text()
CUTOVER = (ROOT / "cutover.sh").read_text()


class SingleRunReleaseContractTests(unittest.TestCase):
    def test_single_entrypoint_progresses_past_isolated_staging(self):
        self.assertIn("prepare_v6_on_mac.sh", SCRIPT)
        self.assertIn("approval_canary", SCRIPT)
        self.assertIn("install.sh --upgrade-stage-only", SCRIPT)
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

    def test_preflight_rejects_split_brain_before_any_staging(self):
        audit = SCRIPT.index('python3 shell_bridge/audit_handover_readiness.py')
        heartbeat = SCRIPT.index('verify_v5_production_health.py remote')
        stage = SCRIPT.index('install.sh --upgrade-stage-only')
        isolated = SCRIPT.index('prepare_v6_on_mac.sh')
        self.assertLess(audit, heartbeat)
        self.assertLess(audit, stage)
        self.assertLess(audit, isolated)
        self.assertIn('if ! PYTHONDONTWRITEBYTECODE=1 python3 shell_bridge/audit_handover_readiness.py >/dev/null; then', SCRIPT)
        self.assertIn('do not create staging or cut over', SCRIPT)

    def test_first_cutover_keeps_candidate_inert_until_accepted(self):
        stage = SCRIPT.index('PROD_PLIST="$PROD_INSTALL/launchagent.plist"')
        stage_only = SCRIPT.index('install.sh --upgrade-stage-only')
        cutover = SCRIPT.index('bash shell_bridge/cutover.sh')
        approval = SCRIPT.index('approval_canary "$PROD_ROOT" "$PROD_ALLOWED" allow production')
        archive = SCRIPT.index('echo "Phase 8: archive stopped production v5')
        publish = SCRIPT.index('python3 "$REGISTRATION_HELPER" publish ')
        receipt = SCRIPT.index('v6-release-acceptance.json')
        self.assertLess(stage, stage_only)
        self.assertLess(stage_only, cutover)
        self.assertLess(cutover, approval)
        self.assertLess(approval, archive)
        self.assertLess(archive, publish)
        self.assertLess(publish, receipt)
        self.assertIn('PLIST="$PROD_PLIST"', SCRIPT)
        self.assertIn('SOURCE_CONFIG_FILE="$LEGACY_CONFIG"', SCRIPT)
        self.assertIn('PROD_PERSIST_PLIST="$HOME/Library/LaunchAgents/$PROD_LABEL.plist"', SCRIPT)
        self.assertIn('reboot_verified":False', SCRIPT)

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

    def test_reversible_archive_is_not_misrepresented_as_sunset_readiness(self):
        archive = SCRIPT.index('echo "Phase 8: archive stopped production v5')
        receipt = SCRIPT.index('"v5_sunset_ready":False')
        self.assertLess(archive, receipt)
        self.assertIn('"acceptance_level":"provisional_handover"', SCRIPT)
        self.assertIn('"recovery_fault_injection_live_verified":False', SCRIPT)
        self.assertIn("V5_SUNSET_READY=0", SCRIPT)
        self.assertIn("V6_PRODUCTION_PROVISIONAL_HANDED_OVER_WITH_RECOVERY_PENDING=1", SCRIPT)
        self.assertNotIn("V6_PRODUCTION_ACCEPTED_WITH_RECOVERY_EVIDENCE_PRESERVED=1", SCRIPT)

    def test_embedded_acceptance_writer_records_actual_provisional_state(self):
        # Run the Python heredoc, not just a source substring assertion.
        start = SCRIPT.index("<<'PYACCEPT'\n") + len("<<'PYACCEPT'\n")
        end = SCRIPT.index("\nPYACCEPT", start)
        embedded = SCRIPT[start:end]
        with tempfile.TemporaryDirectory() as tmp:
            receipt = Path(tmp) / "receipt.json"
            archive = Path(tmp) / "archive"
            subprocess.run(
                [sys.executable, "-c", embedded, str(receipt), "a" * 40,
                 "v6-production-test", str(archive)],
                check=True, capture_output=True, text=True)
            obj = json.loads(receipt.read_text())
            self.assertEqual(obj["source_commit"], "a" * 40)
            self.assertEqual(obj["acceptance_level"], "provisional_handover")
            self.assertIs(obj["v5_sunset_ready"], False)
            self.assertIs(obj["recovery_fault_injection_live_verified"], False)
            self.assertTrue(obj["legacy_state_preserved"])
            self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)

    def test_ssh_smoke_id_expands_to_protocol_valid_identifier(self):
        # Previously the shell assignment ended with a literal "$", which
        # bash -n and all result-verifier tests failed to detect.
        assignment = next(line.strip() for line in SCRIPT.splitlines()
                          if line.strip().startswith('rid="v6-ssh-'))
        value = subprocess.check_output(
            ["/bin/bash", "-c", 'prefix=staging; ' + assignment +
             '; printf "%s" "$rid"'], text=True)
        self.assertIsNotNone(re.fullmatch("v6-ssh-staging-[0-9]{14}-[0-9a-f]{12}", value))
        self.assertNotIn("$", value)

    def test_opted_in_inbound_ssh_requires_separate_device_witness_before_cutover(self):
        local_switch = SCRIPT.index('inbound_ssh_preflight.py')
        challenge = SCRIPT.index('inbound_ssh_witness.py verify')
        cutover = SCRIPT.index('bash shell_bridge/cutover.sh')
        retirement = SCRIPT.index('echo "Phase 8: archive stopped production v5')
        self.assertIn('if [ "${V6_REQUIRE_INBOUND_SSH:-0}" = 1 ]; then', SCRIPT)
        self.assertEqual(SCRIPT.count('if [ "${V6_REQUIRE_INBOUND_SSH:-0}" = 1 ]; then'), 2)
        self.assertIn('INBOUND_SSH_NOT_REQUESTED=1', SCRIPT)
        self.assertIn('INBOUND_SSH_WITNESS_SKIPPED=1', SCRIPT)
        self.assertLess(local_switch, challenge)
        self.assertLess(challenge, cutover)
        self.assertLess(cutover, retirement)
        self.assertIn('SSH_WITNESS_NONCE=', SCRIPT)
        self.assertIn('From a DIFFERENT computer', SCRIPT)
        self.assertIn('external-device SSH login witness missing', SCRIPT)
        self.assertNotIn('systemsetup -setremotelogin', SCRIPT)

    def test_optional_inbound_setting_rejects_invalid_values_before_side_effects(self):
        self.assertIn('case "${V6_REQUIRE_INBOUND_SSH:-0}" in', SCRIPT)
        self.assertIn('0|1) ;;', SCRIPT)
        self.assertIn('V6_REQUIRE_INBOUND_SSH must be exactly 0 or 1', SCRIPT)
        self.assertLess(SCRIPT.index('case "${V6_REQUIRE_INBOUND_SSH:-0}" in'),
                        SCRIPT.index('prepare_v6_on_mac.sh'))

    def test_outbound_ssh_opt_in_covers_enrollment_and_both_canaries(self):
        self.assertIn('case "${V6_ENABLE_OUTBOUND_SSH:-0}" in', SCRIPT)
        self.assertIn('V6_ENABLE_OUTBOUND_SSH must be exactly 0 or 1', SCRIPT)
        self.assertIn('OUTBOUND_SSH_NOT_REQUESTED=1', SCRIPT)
        self.assertIn('STAGING_SSH_CANARY_SKIPPED=1', SCRIPT)
        self.assertIn('PRODUCTION_SSH_CANARY_SKIPPED=1', SCRIPT)
        self.assertIn('SSH policy specified without V6_ENABLE_OUTBOUND_SSH=1', SCRIPT)
        self.assertEqual(SCRIPT.count('if [ "${V6_ENABLE_OUTBOUND_SSH:-0}" = 1 ]; then'), 3)
        self.assertLess(SCRIPT.index('case "${V6_ENABLE_OUTBOUND_SSH:-0}" in'),
                        SCRIPT.index('setup_v6_pinned_ssh.py'))
        self.assertLess(SCRIPT.index('STAGING_SSH_CANARY_SKIPPED=1'),
                        SCRIPT.index('bash shell_bridge/cutover.sh'))
        self.assertGreater(SCRIPT.index('PRODUCTION_SSH_CANARY_SKIPPED=1'),
                           SCRIPT.index('bash shell_bridge/cutover.sh'))

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
