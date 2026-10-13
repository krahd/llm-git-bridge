import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AtomicMigratorTests(unittest.TestCase):
    def make_fixture(self, td):
        home = Path(td) / "home"
        (home / "Library/LaunchAgents").mkdir(parents=True)
        (home / "Library/Logs").mkdir(parents=True)
        suffix = "testcandidate"
        label = "net.laurenzo.local-executor-bridge-v6-" + suffix
        old = "net.laurenzo.local-executor-bridge-v6-staging"
        (home / ("Library/LaunchAgents/" + old + ".plist")).write_text("old")
        (home / ("Library/LaunchAgents/" + label + ".plist")).write_text("new")
        install = home / (".local/share/local-executor-bridge-v6-" + suffix)
        install.mkdir(parents=True)
        (install / "bridge.py").write_text(
            'import sys\nraise SystemExit(0) if len(sys.argv)>1 and sys.argv[1]=="doctor" else None\n')
        for name, data in (
            ("approval_helper.py", "Allow once Reject menu only"),
            ("trusted_operations.py", "registered policy"),
            ("bridge_mailbox_inspector.py", "inspector"),
            ("ssh_pinned_helper.py", "ssh pinned helper"),
        ):
            (install / name).write_text(data)
        app = install / "Local Executor Approval.app"
        app.mkdir()
        cfg = home / (".config/local-executor-bridge-v6-" + suffix) / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({
            "operator_approval_app": str(app), "operator_confirmation_mode": "auto",
            "bridge_instance_id": "iid", "remote": "r:",
            "drive_root_folder_id": "root", "state_dir": str(home / "state")}))
        calls = Path(td) / "calls"
        loaded = Path(td) / "loaded"
        loaded.mkdir()
        (loaded / old).touch()
        fake = Path(td) / "launchctl"
        fake.write_text("""#!/bin/bash
set -u
echo "$@" >> "$CALLS"
case "$1" in
 print) label="${2##*/}"; [ -f "$LOADED/$label" ]; exit $?;;
 bootout)
   label="${2##*/}"; rm -f "$LOADED/$label"
   if [ -f "$LOADED/inject-failed-bootout" ]; then exit 1; fi
   exit 0;;
 bootstrap)
   label="$(basename "$3" .plist)"; touch "$LOADED/$label"; exit 0;;
 kickstart) exit 0;;
 *) exit 0;;
esac
""")
        fake.chmod(0o755)
        return home, suffix, fake, calls, loaded

    def run_migrator(self, home, suffix, fake, calls, loaded, extra=None):
        env = os.environ.copy()
        env.update({
            "MIGRATOR_HOME": str(home), "MIGRATOR_UID": "501",
            "MIGRATOR_LAUNCHCTL": str(fake), "MIGRATOR_SKIP_STAGE": "1",
            "MIGRATOR_SKIP_HEALTH": "1", "CALLS": str(calls),
            "LOADED": str(loaded),
        })
        if extra:
            env.update(extra)
        return subprocess.run(
            ["/bin/bash", str(ROOT / "migrate_v6_staging_atomic.sh"), suffix],
            env=env, text=True, capture_output=True, timeout=12)

    def test_dry_run_does_not_switch_services(self):
        with tempfile.TemporaryDirectory() as td:
            home, suffix, fake, calls, loaded = self.make_fixture(td)
            r = self.run_migrator(home, suffix, fake, calls, loaded, {"MIGRATOR_DRY_RUN": "1"})
            self.assertEqual(r.returncode, 0,
                             (r.stderr, (home / "Library/Logs/LocalExecutorBridgeV6Migrator.log").read_text()))
            self.assertIn("print gui/501/net.laurenzo.local-executor-bridge-v6-staging", calls.read_text())
            self.assertNotIn("bootout", calls.read_text())
            self.assertNotIn("bootstrap", calls.read_text())

    def test_failed_new_owner_stays_on_hold_without_restarting_previous(self):
        with tempfile.TemporaryDirectory() as td:
            home, suffix, fake, calls, loaded = self.make_fixture(td)
            r = self.run_migrator(home, suffix, fake, calls, loaded, {"MIGRATOR_FAIL_AFTER_START": "1"})
            self.assertNotEqual(r.returncode, 0)
            text = calls.read_text()
            self.assertIn("bootout gui/501/net.laurenzo.local-executor-bridge-v6-staging", text)
            self.assertIn("bootout gui/501/net.laurenzo.local-executor-bridge-v6-testcandidate", text)
            self.assertNotIn("bootstrap gui/501 " + str(home / "Library/LaunchAgents/net.laurenzo.local-executor-bridge-v6-staging.plist"), text)
            self.assertNotIn("io.llm-git-bridge.daemon", text)
            hold = home / "Library/Logs/LocalExecutorBridgeV6Migrator.hold"
            self.assertTrue(hold.is_file())
            self.assertIn("Do not restore previous", hold.read_text())

    def test_manual_bootstrap_collision_aborts_before_any_bootout(self):
        with tempfile.TemporaryDirectory() as td:
            home, suffix, fake, calls, loaded = self.make_fixture(td)
            manual = "net.laurenzo.local-executor-bridge-v6-manual-bootstrap"
            (home / ("Library/LaunchAgents/" + manual + ".plist")).write_text("manual")
            (loaded / manual).touch()
            r = self.run_migrator(home, suffix, fake, calls, loaded)
            self.assertNotEqual(r.returncode, 0)
            self.assertNotIn("bootout", calls.read_text())
            self.assertNotIn("bootstrap", calls.read_text())

    def test_loaded_candidate_is_never_booted_out(self):
        with tempfile.TemporaryDirectory() as td:
            home, suffix, fake, calls, loaded = self.make_fixture(td)
            (loaded / ("net.laurenzo.local-executor-bridge-v6-" + suffix)).touch()
            r = self.run_migrator(home, suffix, fake, calls, loaded)
            self.assertNotEqual(r.returncode, 0)
            self.assertNotIn("bootout", calls.read_text())

    def test_prior_reconciliation_hold_refuses_new_attempt(self):
        with tempfile.TemporaryDirectory() as td:
            home, suffix, fake, calls, loaded = self.make_fixture(td)
            hold = home / "Library/Logs/LocalExecutorBridgeV6Migrator.hold"
            hold.write_text("historical work unresolved")
            r = self.run_migrator(home, suffix, fake, calls, loaded)
            self.assertNotEqual(r.returncode, 0)
            self.assertTrue(hold.is_file())
            self.assertNotIn("bootout", calls.read_text() if calls.exists() else "")
            self.assertNotIn("bootstrap", calls.read_text() if calls.exists() else "")

    def test_pre_start_stop_failure_restores_original_from_plist(self):
        with tempfile.TemporaryDirectory() as td:
            home, suffix, fake, calls, loaded = self.make_fixture(td)
            (loaded / "inject-failed-bootout").touch()
            r = self.run_migrator(home, suffix, fake, calls, loaded)
            self.assertNotEqual(r.returncode, 0)
            text = calls.read_text()
            self.assertIn("bootout gui/501/net.laurenzo.local-executor-bridge-v6-staging", text)
            self.assertIn("bootstrap gui/501 " + str(home / "Library/LaunchAgents/net.laurenzo.local-executor-bridge-v6-staging.plist"), text)
            self.assertNotIn("bootstrap gui/501 " + str(home / "Library/LaunchAgents/net.laurenzo.local-executor-bridge-v6-testcandidate.plist"), text)

    def test_source_never_targets_v5(self):
        s = (ROOT / "migrate_v6_staging_atomic.sh").read_text()
        self.assertNotIn("io.llm-git-bridge.daemon", s)
        self.assertNotIn("com.tom.chatgpt-shell-bridge", s)

    def test_app_builder_uses_single_atomic_migrator(self):
        s = (ROOT / "build_v6_staging_migrator_app.sh").read_text()
        self.assertIn("migrate_v6_staging_atomic.sh", s)
        self.assertNotIn("osascript", s)
        self.assertNotIn("LocalAuthentication", s)


if __name__ == "__main__":
    unittest.main()
