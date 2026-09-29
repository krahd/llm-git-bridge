from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from llm_git_bridge.harness import Conflict, HarnessService, SQLiteHarnessStore
from llm_git_bridge.harness.model import LeaseToken


class Clock:
    def __init__(self, value: float = 1000.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "harness.sqlite3"
        self.clock = Clock()
        self.store = SQLiteHarnessStore(self.db)
        self.service = HarnessService(self.store, clock=self.clock)
        self.service.create_job(
            "paper-a",
            title="Paper A",
            goal="Finish the paper",
            repo_url="https://example.invalid/repo.git",
            repo_path="/private/local/path",
            resource_key="papers/a",
            next_action="revise section 2",
            success_condition="tests and review pass",
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_lease_fencing_and_reclaim_after_expiry(self):
        first = self.service.acquire_lease("paper-a", "conversation-a", ttl=10)
        with self.assertRaises(Conflict):
            self.service.acquire_lease("paper-a", "conversation-b", ttl=10)
        self.clock.advance(11)
        second = self.service.acquire_lease("paper-a", "conversation-b", ttl=10)
        self.assertGreater(second.generation, first.generation)
        with self.assertRaises(Conflict):
            self.service.renew_lease(first, ttl=10)
        event_types = [e["event_type"] for e in self.service.events("paper-a")]
        self.assertIn("lease_expired", event_types)

    def test_job_version_is_independent_from_lifecycle_and_lease(self):
        token = self.service.acquire_lease("paper-a", "conversation-a")
        job = self.service.get_job("paper-a")
        before = job["version"]
        updated = self.service.update_job(token, expected_version=before, lifecycle="waiting", waiting_on="review")
        self.assertEqual(updated["lifecycle"], "waiting")
        self.assertGreater(updated["version"], before)
        with self.assertRaises(Conflict):
            self.service.update_job(token, expected_version=before, next_action="stale write")

    def test_handoff_atomically_fences_old_conversation(self):
        old = self.service.acquire_lease("paper-a", "conversation-a")
        handoff = self.service.create_handoff(old, ttl=30)
        with self.assertRaises(Conflict):
            self.service.renew_lease(old)
        with self.assertRaises(Conflict):
            self.service.acquire_lease("paper-a", "conversation-c")
        new = self.service.claim_handoff("paper-a", handoff.nonce, "conversation-b")
        self.assertGreater(new.generation, old.generation)
        with self.assertRaises(Conflict):
            self.service.claim_handoff("paper-a", handoff.nonce, "conversation-c")

    def test_expired_handoff_returns_job_to_normal_acquisition(self):
        old = self.service.acquire_lease("paper-a", "conversation-a")
        handoff = self.service.create_handoff(old, ttl=5)
        self.clock.advance(6)
        with self.assertRaises(Conflict):
            self.service.claim_handoff("paper-a", handoff.nonce, "conversation-b")
        fresh = self.service.acquire_lease("paper-a", "conversation-c")
        self.assertGreater(fresh.generation, old.generation)

    def test_operation_idempotency_and_terminal_replay(self):
        token = self.service.acquire_lease("paper-a", "conversation-a")
        first = self.service.start_operation(
            token,
            idempotency_key="push-main-001",
            kind="git-push",
            target="origin/main",
            intended_effect="publish checkpoint",
        )
        second = self.service.start_operation(
            token,
            idempotency_key="push-main-001",
            kind="git-push",
            target="origin/main",
            intended_effect="publish checkpoint",
        )
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.operation_id, second.operation_id)
        done = self.service.finish_operation(token, first.operation_id, status="completed", result={"sha": "abc"})
        replay = self.service.finish_operation(token, first.operation_id, status="completed", result={"ignored": True})
        self.assertEqual(done["result_json"], replay["result_json"])
        with self.assertRaises(Conflict):
            self.service.finish_operation(token, first.operation_id, status="failed")

    def test_database_reopen_preserves_job_and_fencing_generation(self):
        first = self.service.acquire_lease("paper-a", "conversation-a", ttl=5)
        self.store.close()
        self.clock.advance(6)
        self.store = SQLiteHarnessStore(self.db)
        self.service = HarnessService(self.store, clock=self.clock)
        job = self.service.get_job("paper-a")
        self.assertEqual(job["title"], "Paper A")
        second = self.service.acquire_lease("paper-a", "conversation-b")
        self.assertGreater(second.generation, first.generation)

    def test_continuation_projection_strips_private_repo_path(self):
        token = self.service.acquire_lease("paper-a", "conversation-a")
        op = self.service.start_operation(token, idempotency_key="build-001", kind="test")
        capsule = self.service.continuation("paper-a")
        self.assertNotIn("repo_path", capsule["job"])
        self.assertEqual(capsule["operations"][0]["operation_id"], op.operation_id)
        self.assertEqual(capsule["lease"]["generation"], token.generation)

    def test_old_lease_token_cannot_update_after_release_and_reacquire(self):
        old = self.service.acquire_lease("paper-a", "conversation-a")
        self.service.release_lease(old)
        new = self.service.acquire_lease("paper-a", "conversation-a")
        self.assertGreater(new.generation, old.generation)
        job = self.service.get_job("paper-a")
        with self.assertRaises(Conflict):
            self.service.update_job(old, expected_version=job["version"], next_action="bad")

    def test_browser_start_reservation_fences_duplicate_opening_and_recycles(self):
        old = self.service.acquire_lease("paper-a", "conversation-a")
        handoff = self.service.create_handoff(old, ttl=300)
        first = self.service.reserve_reentry("browser-a", ttl=30)
        self.assertEqual(first["handoff_id"], handoff.handoff_id)
        self.assertTrue(first["new_reservation"])
        repeated = self.service.reserve_reentry("browser-a", ttl=30)
        self.assertEqual(repeated["handoff_id"], handoff.handoff_id)
        self.assertFalse(repeated["new_reservation"])
        self.assertIsNone(self.service.reserve_reentry("browser-b", ttl=30))
        with self.assertRaises(Conflict):
            self.service.acquire_lease("paper-a", "conversation-c")
        self.clock.advance(31)
        recycled = self.service.reserve_reentry("browser-b", ttl=30)
        self.assertEqual(recycled["handoff_id"], handoff.handoff_id)
        self.assertTrue(recycled["new_reservation"])

    def test_claim_from_starting_clears_browser_reservation(self):
        old = self.service.acquire_lease("paper-a", "conversation-a")
        handoff = self.service.create_handoff(old, ttl=300)
        self.service.reserve_reentry("browser-a", ttl=60)
        new = self.service.claim_handoff("paper-a", handoff.nonce, "conversation-b")
        self.assertGreater(new.generation, old.generation)
        row = self.store.conn.execute(
            "SELECT state,nonce_secret,starting_by,starting_at,starting_expires_at FROM handoffs WHERE handoff_id=?",
            (handoff.handoff_id,),
        ).fetchone()
        self.assertEqual(row["state"], "claimed")
        self.assertIsNone(row["nonce_secret"])
        self.assertIsNone(row["starting_by"])
        self.assertIsNone(row["starting_at"])
        self.assertIsNone(row["starting_expires_at"])

    def test_release_reentry_returns_handoff_to_pending(self):
        old = self.service.acquire_lease("paper-a", "conversation-a")
        handoff = self.service.create_handoff(old, ttl=300)
        self.service.reserve_reentry("browser-a", ttl=60)
        with self.assertRaises(Conflict):
            self.service.release_reentry("browser-b", handoff.handoff_id)
        released = self.service.release_reentry("browser-a", handoff.handoff_id)
        self.assertTrue(released["released"])
        next_item = self.service.reserve_reentry("browser-b", ttl=60)
        self.assertTrue(next_item["new_reservation"])

    def test_overall_handoff_expiry_wins_over_starting_reservation(self):
        old = self.service.acquire_lease("paper-a", "conversation-a")
        handoff = self.service.create_handoff(old, ttl=5)
        self.service.reserve_reentry("browser-a", ttl=30)
        self.clock.advance(6)
        self.assertIsNone(self.service.reserve_reentry("browser-b", ttl=30))
        row = self.store.conn.execute(
            "SELECT state,nonce_secret,starting_by FROM handoffs WHERE handoff_id=?",
            (handoff.handoff_id,),
        ).fetchone()
        self.assertEqual(row["state"], "expired")
        self.assertIsNone(row["nonce_secret"])
        self.assertIsNone(row["starting_by"])

    def test_starting_reservation_survives_database_reopen(self):
        old = self.service.acquire_lease("paper-a", "conversation-a")
        handoff = self.service.create_handoff(old, ttl=300)
        self.service.reserve_reentry("browser-a", ttl=60)
        self.store.close()
        self.store = SQLiteHarnessStore(self.db)
        self.service = HarnessService(self.store, clock=self.clock)
        capsule = self.service.continuation("paper-a")
        self.assertEqual(capsule["handoff"]["handoff_id"], handoff.handoff_id)
        self.assertEqual(capsule["handoff"]["state"], "starting")
        self.assertEqual(capsule["handoff"]["starting_by"], "browser-a")

    def test_schema_v1_database_migrates_starting_reservation_fields(self):
        legacy = Path(self.tmp.name) / "legacy.sqlite3"
        conn = sqlite3.connect(legacy)
        conn.executescript(
            """
            CREATE TABLE harness_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT INTO harness_meta(key,value) VALUES('schema_version','1');
            CREATE TABLE handoffs(
                handoff_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                nonce_hash TEXT NOT NULL UNIQUE,
                nonce_secret TEXT,
                state TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                claimed_at REAL,
                claimed_by TEXT
            );
            CREATE UNIQUE INDEX one_pending_handoff_per_job
                ON handoffs(job_id) WHERE state='pending';
            """
        )
        conn.close()
        migrated = SQLiteHarnessStore(legacy)
        try:
            cols = {row[1] for row in migrated.conn.execute("PRAGMA table_info(handoffs)").fetchall()}
            self.assertTrue({"starting_by", "starting_at", "starting_expires_at"}.issubset(cols))
            version = migrated.conn.execute(
                "SELECT value FROM harness_meta WHERE key='schema_version'"
            ).fetchone()[0]
            self.assertEqual(version, "2")
            indexes = {row[1] for row in migrated.conn.execute("PRAGMA index_list(handoffs)").fetchall()}
            self.assertIn("one_open_handoff_per_job", indexes)
            self.assertIn("one_starting_handoff_per_client", indexes)
            self.assertNotIn("one_pending_handoff_per_job", indexes)
        finally:
            migrated.close()


if __name__ == "__main__":
    unittest.main()
