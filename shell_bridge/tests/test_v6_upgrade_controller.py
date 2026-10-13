import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CUTOVER = ROOT / "cutover.sh"
UPGRADE = ROOT / "upgrade_v6.sh"


class V6UpgradeControllerTests(unittest.TestCase):
    def test_scripts_have_valid_bash_syntax(self):
        for path in (UPGRADE, CUTOVER):
            with self.subTest(path=path):
                result = subprocess.run(["bash", "-n", str(path)],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_entrypoint_refuses_noninteractive_non_macos_before_side_effects(self):
        result = subprocess.run(["bash", str(UPGRADE)],
                                capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("V6_UPGRADE_HOLD:", result.stderr)

    def test_v6_mode_is_never_silent_or_bridge_controlled(self):
        c = CUTOVER.read_text()
        u = UPGRADE.read_text()
        self.assertIn('CUTOVER_SOURCE_VERSION="$' + '{CUTOVER_SOURCE_VERSION:-5}"', c)
        self.assertIn('export CUTOVER_SOURCE_VERSION=6', u)
        self.assertIn('out-of-band interactive local Terminal', c)
        self.assertIn('CHATGPT_SHELL_BRIDGE_REQUEST_ID', c)
        self.assertIn('source version', c.lower())
        self.assertIn('legacy retirement requires separate verified production acceptance', c)
        self.assertIn('exact 40-hex revision', u)

    def test_v6_source_requires_exact_state_health_and_new_build(self):
        c = CUTOVER.read_text()
        stop = c.index("# Stop old consumers first.")
        self.assertLess(c.index("staged v6 install manifest integrity check failed"), stop)
        self.assertLess(c.index("cannot read current production health.json"), stop)
        self.assertLess(c.index("journal contains unresolved or indeterminate effects"), stop)
        self.assertLess(c.index("v6-to-v6 source pre-stop identity and quiescence gate did not pass"), stop)
        self.assertIn("v6_to_v6_preflight.py", c)
        self.assertIn('v6-to-v6 candidate does not preserve the live durable state path', c)
        self.assertIn("candidate_manifest_verified=True", c)
        self.assertIn("pending_mailbox_files=[]", c)

    def test_old_source_drains_twice_and_ambiguous_new_start_holds(self):
        c = CUTOVER.read_text()
        stop = c.index("# Stop old consumers first.")
        start = c.index('launchctl bootstrap "gui/$' + '{UID_NOW}" "$NEW_PLIST"')
        self.assertLess(stop, c.index("PENDING_AFTER_STOP=", stop))
        self.assertLess(c.index("PENDING_AFTER_STOP=", stop), start)
        self.assertEqual(c.count('verify_quiescent_journal.py" "$LEGACY_STATE_DIR"'), 2)
        self.assertLess(c.index("STARTED_NEW=1"), start)
        self.assertIn("cutover-reconciliation-required", c)
        self.assertIn("CUTOVER_COMMITTED=1", c)
        # A newly indeterminate journal after stopping v6 cannot silently
        # restart the original service, and a second loaded owner must block
        # acceptance before a production smoke request is submitted.
        second_journal = c.rindex('verify_quiescent_journal.py" "$LEGACY_STATE_DIR"')
        hold_mark = c.rindex("V6_UNSAFE_TO_RESTORE=1")
        self.assertLess(hold_mark, second_journal)
        self.assertIn("V6_ACTIVATED_OWNERSHIP=", c)
        self.assertLess(c.index("V6_ACTIVATED_OWNERSHIP="),
                        c.index("# End-to-end production smoke:"))
        self.assertIn("v6 replacement is not the exclusive loaded mailbox owner", c)
        self.assertIn("v6_upgrade_owner_race_hold", c)
        rollback = c.split("rollback() {", 1)[1].split("trap rollback EXIT", 1)[0]
        # Restoring an old v6 process after an ambiguous stop also requires
        # a *fresh* locally verified journal and rechecked loaded ownership.
        self.assertIn("PYROLLBACKOWN", rollback)
        self.assertIn("verify_quiescent_journal.py", rollback)
        self.assertLess(rollback.index("verify_quiescent_journal.py"),
                        rollback.index("restoring previously active bridge service"))
        self.assertIn("V6_UNSAFE_TO_RESTORE=1", rollback)


if __name__ == "__main__":
    unittest.main()
