import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from verify_v6_candidate_health import HealthVerificationError, verify

SHA = "a" * 40
NOW = dt.datetime(2026, 10, 10, 7, 0, tzinfo=dt.timezone.utc)


class CandidateHealthGateTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "bridge_instance_id": "instance-1",
            "drive_root_folder_id": "root-012345",
            "requests_folder_id": "req-7f",
            "results_folder_id": "res-001",
        }
        self.health = dict(self.cfg, bridge_version="6", build_source_commit=SHA,
                           build_code_integrity="matches_manifest",
                           publisher_pid=1738, updated_at=NOW.isoformat())
        self.launch = '\t"PID" = 1738;\n'

    def validate(self):
        return verify(self.cfg, self.health, SHA, "test-label",
                      self.launch, now=NOW)

    def test_matches_exact_live_build(self):
        self.assertEqual(self.validate(), 1738)

    def test_rejects_stale_or_future_heartbeat(self):
        for offset in (-181, 31):
            with self.subTest(offset=offset):
                self.health["updated_at"] = (NOW + dt.timedelta(seconds=offset)).isoformat()
                with self.assertRaises(HealthVerificationError):
                    self.validate()

    def test_rejects_unrelated_or_dead_pid(self):
        self.launch = '\t"PID" = 99;\n'
        with self.assertRaises(HealthVerificationError):
            self.validate()
        self.launch = ''
        with self.assertRaises(HealthVerificationError):
            self.validate()

    def test_rejects_mismatched_source_and_integrity(self):
        for key, value in (("build_source_commit", "b"*40),
                           ("build_code_integrity", "mismatch"),
                           ("bridge_version", "5"),
                           ("requests_folder_id", "other")):
            with self.subTest(key=key):
                old = self.health[key]
                self.health[key] = value
                with self.assertRaises(HealthVerificationError):
                    self.validate()
                self.health[key] = old

    def test_rejects_missing_identity_values(self):
        for key in ("bridge_instance_id", "drive_root_folder_id",
                    "requests_folder_id", "results_folder_id"):
            with self.subTest(key=key):
                old = self.cfg[key]
                self.cfg[key] = ""
                with self.assertRaises(HealthVerificationError):
                    self.validate()
                self.cfg[key] = old

    def test_requires_explicit_timezone(self):
        self.health["updated_at"] = "2026-10-10T07:00:00"
        with self.assertRaises(HealthVerificationError):
            self.validate()


if __name__ == "__main__":
    unittest.main()
