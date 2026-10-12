import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import verify_v5_production_health as check


class LiveProductionV5PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config_path = self.root / "config.json"
        self.health_path = self.root / "health.json"
        self.now = dt.datetime(2026, 10, 11, 23, 0, tzinfo=dt.timezone.utc)
        self.config = {
            "drive_root_folder_id": check.PRODUCTION_ROOT,
            "bridge_instance_id": "legacy-one",
            "remote": "chatgpt-git-bridge:",
        }
        self.health = {
            "kind": "shell_bridge_health",
            "bridge_version": "5",
            "drive_root_folder_id": check.PRODUCTION_ROOT,
            "bridge_instance_id": "legacy-one",
            "updated_at": (self.now - dt.timedelta(seconds=5)).isoformat(),
        }
        self.write()

    def write(self):
        self.config_path.write_text(json.dumps(self.config))
        self.health_path.write_text(json.dumps(self.health))

    def verify(self):
        loaded = check.load_legacy_config(self.config_path)
        check.verify_v5_production_health(loaded, self.health,
                                          now=self.now.timestamp())

    def test_fresh_pinned_v5_is_accepted(self):
        self.verify()
        self.assertEqual(check.load_legacy_config(self.config_path)["remote"],
                         "chatgpt-git-bridge:")

    def test_v6_live_heartbeat_must_not_be_called_v5(self):
        self.health["bridge_version"] = "6"
        with self.assertRaisesRegex(check.ProductionIdentityError, "already reports v6"):
            self.verify()

    def test_unknown_or_boolean_versions_are_not_accepted(self):
        for version in ("unknown", 6, True, None):
            with self.subTest(version=version):
                self.health["bridge_version"] = version
                with self.assertRaises(check.ProductionIdentityError):
                    self.verify()

    def test_mailbox_and_instance_must_match(self):
        for field, value in (("drive_root_folder_id", "other"),
                             ("bridge_instance_id", "unexpected"),
                             ("kind", "other")):
            with self.subTest(field=field):
                old = self.health[field]
                self.health[field] = value
                with self.assertRaises(check.ProductionIdentityError):
                    self.verify()
                self.health[field] = old

    def test_missing_or_stale_or_future_heartbeat_rejected(self):
        for offset in (-190, +45):
            with self.subTest(offset=offset):
                self.health["updated_at"] = (
                    self.now + dt.timedelta(seconds=offset)).isoformat()
                with self.assertRaisesRegex(check.ProductionIdentityError,
                                            "stale or from the future"):
                    self.verify()
        self.health.pop("updated_at")
        with self.assertRaises(check.ProductionIdentityError):
            self.verify()

    def test_unsafe_legacy_config_is_rejected(self):
        self.config["drive_root_folder_id"] = "wrong"
        self.write()
        with self.assertRaises(check.ProductionIdentityError):
            check.load_legacy_config(self.config_path)
        self.config["drive_root_folder_id"] = check.PRODUCTION_ROOT
        self.config["remote"] = "untrusted"
        self.write()
        with self.assertRaises(check.ProductionIdentityError):
            check.load_legacy_config(self.config_path)
        self.config["remote"] = "chatgpt-git-bridge:"
        self.write()
        external = self.root / "external.json"
        external.write_text(self.config_path.read_text())
        self.config_path.unlink()
        self.config_path.symlink_to(external)
        with self.assertRaises(check.ProductionIdentityError):
            check.load_legacy_config(self.config_path)

    def test_cutover_rechecks_version_after_staging(self):
        script = (Path(__file__).resolve().parents[1] / "cutover.sh").read_text()
        self.assertIn("production mailbox is not owned by v5", script)
        self.assertIn('"$TMP_HEALTH" "$ROOT_ID" "$TARGET_INSTANCE"', script)
        self.assertLess(script.index("production mailbox is not owned by v5"),
                        script.index("# Stop old consumers first."))

    def test_release_runs_preflight_before_ssh_enrollment_and_staging(self):
        script = (Path(__file__).resolve().parents[1] / "v6_release_once.sh").read_text()
        self.assertIn("Phase 0: verify live production", script)
        self.assertLess(script.index("verify_v5_production_health.py verify"),
                        script.index("setup_v6_pinned_ssh.py"))
        self.assertLess(script.index("verify_v5_production_health.py verify"),
                        script.index("prepare_v6_on_mac.sh"))
        self.assertIn("reconcile installed owners before release", script)
        self.assertIn("bridge_service_ownership.py", script)
        self.assertLess(script.index("bridge_service_ownership.py"),
                        script.index("prepare_v6_on_mac.sh"))


if __name__ == "__main__":
    unittest.main()
