import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from v6_to_v6_preflight import assess


class V6ToV6PrestopTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 10, 12, 18, 0, tzinfo=dt.timezone.utc)
        self.old = "net.laurenzo.local-executor-bridge"
        self.new = "net.laurenzo.local-executor-bridge-v6-next"
        self.source_config = {
            "bridge_instance_id": "existing-instance",
            "drive_root_folder_id": "production-root",
            "requests_folder_id": "production-requests",
            "results_folder_id": "production-results",
            "state_dir": "/Users/test/.local/state/production",
        }
        self.candidate_config = dict(self.source_config)
        self.record = {
            "label": self.old, "pid": "1786", "running": True,
            "state": "verified", **self.source_config,
        }
        self.inventory = {
            "kind": "loaded_bridge_ownership", "mutations_performed": False,
            "loaded": [self.record], "safe_to_stage": True,
            "problems": [], "active_collisions": [],
        }
        self.health = {
            "bridge_version": "6", "publisher_pid": 1786,
            "updated_at": self.now.isoformat(), "active_requests": [],
            "pending_approvals": [], "state": {"started_without_finished": 0},
            **{k: v for k, v in self.source_config.items() if k != "state_dir"},
        }

    def evaluate(self, **updates):
        data = {
            "inventory": self.inventory, "source_label": self.old,
            "candidate_label": self.new, "source_config": self.source_config,
            "candidate_config": self.candidate_config, "health": self.health,
            "journal_verified": True, "pending_mailbox_files": [],
            "candidate_source_commit": "a" * 40,
            "candidate_manifest_verified": True, "now": self.now,
        }
        data.update(updates)
        return assess(**data)

    def test_clean_single_owner_can_only_pass_prestop_never_activate(self):
        report = self.evaluate()
        self.assertEqual(report["blockers"], [])
        self.assertTrue(report["prestop_evidence_consistent"])
        self.assertFalse(report["activation_authorized"])
        self.assertFalse(report["retirement_authorized"])
        self.assertFalse(report["mutations_performed"])

    def test_observed_live_split_brain_and_parked_jobs_fail(self):
        other = {
            "label": "io.llm-git-bridge.daemon", "pid": "1738",
            "running": True, "state": "verified", **self.source_config,
        }
        staging = {
            "label": "net.laurenzo.local-executor-bridge-v6-manual-bootstrap",
            "pid": "1790", "running": True, "state": "verified",
            "drive_root_folder_id": "stage-root",
            "requests_folder_id": "stage-requests",
            "results_folder_id": "stage-results",
            "state_dir": "/Users/test/.local/state/stage",
            "bridge_instance_id": "stage",
        }
        parked = dict(staging, label="stage-parked", pid=None, running=False)
        inventory = dict(
            self.inventory, safe_to_stage=False,
            loaded=[self.record, other, staging, parked],
            problems=["shared production journal", "shared staging mailbox"],
            active_collisions=["production and staging conflicts"],
        )
        report = self.evaluate(inventory=inventory)
        self.assertIn("ownership_not_exclusive", report["blockers"])
        self.assertIn("other_loaded_mailbox_or_state_claimant", report["blockers"])
        self.assertFalse(report["prestop_evidence_consistent"])

    def test_parked_owner_claiming_production_blocks(self):
        parked = dict(self.record, label="legacy-parked", pid=None, running=False)
        inventory = dict(self.inventory, loaded=[self.record, parked])
        report = self.evaluate(inventory=inventory)
        self.assertIn("other_loaded_mailbox_or_state_claimant", report["blockers"])

    def test_no_complete_journal_or_unknown_queue_blocks(self):
        for kw, expected in (
            ({"journal_verified": False}, "durable_journal_not_fully_verified"),
            ({"pending_mailbox_files": None}, "remote_requests_not_proven_empty"),
            ({"pending_mailbox_files": ["a2.json"]}, "remote_requests_not_proven_empty"),
        ):
            with self.subTest(kw=kw):
                self.assertIn(expected, self.evaluate(**kw)["blockers"])

    def test_stale_health_or_wrong_pid_or_version_blocks(self):
        for changes, expected in (
            ({"publisher_pid": 1738}, "source_health_pid_disagrees"),
            ({"updated_at": "2020-01-01T00:00:00Z"}, "source_health_not_fresh"),
            ({"bridge_version": "5"}, "source_is_not_verified_v6"),
            ({"active_requests": ["inflight"]}, "source_has_active_requests_or_approvals"),
            ({"state": {"started_without_finished": 1}}, "source_health_unresolved_state"),
        ):
            with self.subTest(changes=changes):
                self.assertIn(expected, self.evaluate(
                    health={**self.health, **changes})["blockers"])

    def test_candidate_must_share_exact_existing_mailbox_and_journal(self):
        for field in ("bridge_instance_id", "drive_root_folder_id",
                      "requests_folder_id", "results_folder_id", "state_dir"):
            with self.subTest(field=field):
                candidate = dict(self.candidate_config, **{field: "different"})
                self.assertIn("candidate_identity_or_journal_mismatch", self.evaluate(
                    candidate_config=candidate)["blockers"])

    def test_manifest_commit_and_loaded_candidate_fail_closed(self):
        for kw, expected in (
            ({"candidate_manifest_verified": False}, "candidate_install_integrity_unverified"),
            ({"candidate_source_commit": "not-a-commit"}, "candidate_revision_not_pinned"),
            ({"candidate_label": self.old}, "invalid_distinct_service_labels"),
        ):
            with self.subTest(kw=kw):
                self.assertIn(expected, self.evaluate(**kw)["blockers"])
        loaded = dict(self.record, label=self.new)
        inventory = dict(self.inventory, loaded=[self.record, loaded])
        self.assertIn("candidate_already_loaded",
                      self.evaluate(inventory=inventory)["blockers"])

    def test_unverifiable_or_missing_source_fails_closed(self):
        inventory = dict(self.inventory, loaded=[], safe_to_stage=False)
        self.assertIn("source_not_single_running_service",
                      self.evaluate(inventory=inventory)["blockers"])
        inventory = dict(self.inventory, loaded=[{"label": self.old,
                                                  "state": "unverifiable"}])
        self.assertIn("unverifiable_loaded_services",
                      self.evaluate(inventory=inventory)["blockers"])


if __name__ == "__main__":
    unittest.main()
