from __future__ import annotations

from pathlib import Path
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

    def test_operation_idempotency_key_is_bound_to_operation_payload(self):
        token = self.service.acquire_lease("paper-a", "conversation-a")
        self.service.start_operation(
            token,
            idempotency_key="push-main-bound-001",
            kind="git-push",
            target="origin/main",
            intended_effect="publish checkpoint",
        )
        with self.assertRaisesRegex(Conflict, "different operation payload"):
            self.service.start_operation(
                token,
                idempotency_key="push-main-bound-001",
                kind="delete",
                target="other",
                intended_effect="different effect",
            )

    def test_new_pairing_code_invalidates_older_unconsumed_code(self):
        old = self.service.create_pairing_code(ttl=300)["code"]
        new = self.service.create_pairing_code(ttl=300)["code"]
        with self.assertRaises(Conflict):
            self.service.consume_pairing_code(old, "browser-old")
        paired = self.service.consume_pairing_code(new, "browser-new")
        self.assertEqual(paired["client_id"], "browser-new")

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


if __name__ == "__main__":
    unittest.main()
