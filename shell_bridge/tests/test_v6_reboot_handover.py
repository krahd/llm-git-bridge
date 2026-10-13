from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
CUTOVER = ROOT / "cutover.sh"


class RebootSafeV6HandoverOrderTests(unittest.TestCase):
    def test_script_is_valid_bash(self):
        result = subprocess.run(["bash", "-n", str(CUTOVER)],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_old_login_owner_archived_before_process_stop(self):
        s = CUTOVER.read_text()
        marker = 'python3 "$V6_REGISTRAR" archive '
        self.assertIn(marker, s)
        self.assertLess(s.index("verify_v6_login_claimants.py"), s.index(marker))
        self.assertLess(s.index(marker), s.index("# Stop old consumers first."))
        self.assertIn("V6_ARCHIVE_STARTED=1", s)
        self.assertIn("V6_OLD_SOURCE_PLIST=", s)

    def test_exact_build_health_before_new_login_registration_and_smoke(self):
        s = CUTOVER.read_text()
        verify = s.index('python3 "$HEALTH_VERIFIER"')
        publish = s.index('python3 "$V6_REGISTRAR" publish ')
        owners = s.index("V6_ACTIVATED_OWNERSHIP=")
        smoke = s.index("# End-to-end production smoke:")
        self.assertLess(verify, publish)
        self.assertLess(publish, owners)
        self.assertLess(owners, smoke)
        self.assertIn("V6_TARGET_PERSISTED=1", s)

    def test_ambiguous_new_admission_quarantines_restart_registration(self):
        s = CUTOVER.read_text()
        rollback = s.split("rollback() {", 1)[1].split("trap rollback EXIT", 1)[0]
        self.assertIn('python3 "$V6_REGISTRAR" quarantine ', rollback)
        self.assertIn("cutover-reconciliation-required", rollback)
        self.assertIn('python3 "$V6_REGISTRAR" restore ', rollback)
        self.assertLess(rollback.index("verify_quiescent_journal.py"),
                        rollback.index('python3 "$V6_REGISTRAR" restore '))
        self.assertLess(rollback.index("verify_pending_approvals.py"),
                        rollback.index('python3 "$V6_REGISTRAR" restore '))

    def test_success_persists_reboot_plan_but_never_claims_verified_reboot(self):
        s = CUTOVER.read_text()
        self.assertIn("v6-upgrade-restart-registration.txt", s)
        self.assertIn("reboot_verified=0", s)
        self.assertIn("CUTOVER_COMMITTED=1", s)
        self.assertIn("RETIRED_OLD: 0", s)


if __name__ == "__main__":
    unittest.main()
