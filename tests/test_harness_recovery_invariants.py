from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from llm_git_bridge.harness.errors import Conflict
from llm_git_bridge.harness.service import HarnessService
from llm_git_bridge.harness.sqlite_store import SQLiteHarnessStore


class RecoveryInvariantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteHarnessStore(Path(self.tmp.name) / "harness.sqlite3")
        self.service = HarnessService(self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _job_and_lease(self, job_id="recovery-job"):
        self.service.create_job(job_id, title="Recovery", goal="Verify recovery invariants")
        return self.service.acquire_lease(job_id, "holder-a", ttl=300)

    def test_claimed_handoff_cannot_mutate_until_started_operation_resolves(self):
        lease1 = self._job_and_lease()
        op = self.service.start_operation(lease1, idempotency_key="op-one", kind="git")
        handoff = self.service.create_handoff(lease1, ttl=300)
        lease2 = self.service.claim_handoff("recovery-job", handoff.nonce, "holder-b", ttl=300)
        version = self.service.get_job("recovery-job")["version"]
        with self.assertRaisesRegex(Conflict, "unresolved operation"):
            self.service.update_job(lease2, expected_version=version, next_action="unsafe")
        with self.assertRaisesRegex(Conflict, "unresolved operation"):
            self.service.start_operation(lease2, idempotency_key="op-two", kind="git")
        self.service.finish_operation(lease2, op.operation_id, status="completed", result={"verified": True})
        updated = self.service.update_job(lease2, expected_version=version, next_action="safe")
        self.assertEqual(updated["next_action"], "safe")

    def test_indeterminate_operation_requires_explicit_reconciliation(self):
        lease = self._job_and_lease("indeterminate-job")
        op = self.service.start_operation(lease, idempotency_key="op-indeterminate", kind="git")
        self.service.finish_operation(lease, op.operation_id, status="indeterminate")
        version = self.service.get_job("indeterminate-job")["version"]
        with self.assertRaisesRegex(Conflict, "unresolved operation"):
            self.service.update_job(lease, expected_version=version, next_action="must not run")
        reconciled = self.service.finish_operation(lease, op.operation_id, status="failed", result={"verified": "not applied"})
        self.assertEqual(reconciled["status"], "failed")
        updated = self.service.update_job(lease, expected_version=version, next_action="replanned delta")
        self.assertEqual(updated["next_action"], "replanned delta")

    def test_terminal_job_is_immutable_and_cannot_be_released_then_reacquired(self):
        lease = self._job_and_lease("terminal-job")
        version = self.service.get_job("terminal-job")["version"]
        terminal = self.service.update_job(lease, expected_version=version, lifecycle="completed")
        with self.assertRaisesRegex(Conflict, "terminal"):
            self.service.update_job(lease, expected_version=terminal["version"], title="Changed")
        with self.assertRaisesRegex(Conflict, "terminal"):
            self.service.create_handoff(lease)
        self.service.release_lease(lease)
        with self.assertRaisesRegex(Conflict, "terminal"):
            self.service.acquire_lease("terminal-job", "holder-c")

    def test_illegal_lifecycle_transition_is_rejected(self):
        lease = self._job_and_lease("blocked-job")
        version = self.service.get_job("blocked-job")["version"]
        blocked = self.service.update_job(lease, expected_version=version, lifecycle="blocked")
        with self.assertRaisesRegex(Conflict, "illegal lifecycle transition"):
            self.service.update_job(lease, expected_version=blocked["version"], lifecycle="completed")
        active = self.service.update_job(lease, expected_version=blocked["version"], lifecycle="active")
        self.assertEqual(active["lifecycle"], "active")


if __name__ == "__main__":
    unittest.main()
