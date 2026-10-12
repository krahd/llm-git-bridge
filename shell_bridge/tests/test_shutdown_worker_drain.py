"""Shutdown regression: held mailbox lock must outlive every request worker."""
from pathlib import Path
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge


class WorkerDrainBeforeUnlockTests(unittest.TestCase):
    def test_shutdown_drain_waits_for_live_worker(self):
        start = threading.Event()
        release = threading.Event()
        drained = threading.Event()
        lock_released = threading.Event()

        def worker_body():
            start.set()
            release.wait(timeout=5)

        worker = threading.Thread(target=worker_body, daemon=True)
        worker.start()
        self.assertTrue(start.wait(1))

        def shutdown_body():
            bridge._drain_workers_before_unlock({worker})
            drained.set()
            lock_released.set()

        waiter = threading.Thread(target=shutdown_body, daemon=True)
        waiter.start()
        try:
            self.assertFalse(drained.wait(timeout=0.15),
                             "shutdown must not complete while worker runs")
            self.assertFalse(lock_released.is_set())
        finally:
            release.set()
        waiter.join(timeout=2)
        worker.join(timeout=2)
        self.assertFalse(waiter.is_alive())
        self.assertFalse(worker.is_alive())
        self.assertTrue(drained.is_set())
        self.assertTrue(lock_released.is_set())

    def test_draining_already_finished_workers_is_idempotent(self):
        finished = threading.Thread(target=lambda: None)
        finished.start()
        finished.join()
        bridge._drain_workers_before_unlock({finished})
        bridge._drain_workers_before_unlock(set())

    def test_daemon_source_orders_drain_before_unlock_with_no_timeout(self):
        text = (Path(__file__).resolve().parents[1] / "bridge.py").read_text()
        body = text[text.index("def daemon(config_path: Path)"):text.index("def once(config_path: Path)")]
        self.assertIn("_drain_workers_before_unlock(threads)", body)
        self.assertLess(body.index("_drain_workers_before_unlock(threads)"),
                        body.index("lock_file.close()"))
        self.assertNotIn("t.join(timeout=", body)
        self.assertNotIn("deadline = time.monotonic() + 5", body)


if __name__ == "__main__":
    unittest.main()
