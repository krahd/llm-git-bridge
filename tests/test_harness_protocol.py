from __future__ import annotations

import json
from pathlib import Path
import stat
import tempfile
import threading
import unittest

from llm_git_bridge.harness.daemon import HarnessUnixServer, request
from llm_git_bridge.harness.local_protocol import LocalProtocol
from llm_git_bridge.harness.runtime import APP_ID, state_dir
from llm_git_bridge.harness.service import HarnessService
from llm_git_bridge.harness.sqlite_store import SQLiteHarnessStore


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = SQLiteHarnessStore(self.root / "harness.sqlite3")
        self.protocol = LocalProtocol(HarnessService(self.store))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_mutation_request_is_cached_and_payload_bound(self):
        msg = {
            "protocol": 1,
            "request_id": "create-paper-a-001",
            "action": "create_job",
            "args": {"job_id": "paper-a", "title": "Paper A", "goal": "Finish it"},
        }
        first = self.protocol.dispatch(msg)
        second = self.protocol.dispatch(msg)
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(second["replayed"])
        changed = json.loads(json.dumps(msg))
        changed["args"]["title"] = "Different"
        conflict = self.protocol.dispatch(changed)
        self.assertFalse(conflict["ok"])
        self.assertEqual(conflict["error"]["type"], "conflict")

    def test_started_request_replay_is_indeterminate_not_reexecuted(self):
        args = {"job_id": "paper-a", "title": "Paper A", "goal": "Finish it"}
        fp = __import__("hashlib").sha256(
            json.dumps({"action": "create_job", "args": args}, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO protocol_requests(request_id,fingerprint,action,status,created_at) VALUES(?,?,?,'started',0)",
                ("req-indeterminate-001", fp, "create_job"),
            )
        response = self.protocol.dispatch({"protocol": 1, "request_id": "req-indeterminate-001", "action": "create_job", "args": args})
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["type"], "indeterminate_request")
        with self.assertRaises(Exception):
            self.protocol.service.get_job("paper-a")

    def test_unexpected_failure_leaves_started_journal(self):
        original = self.protocol._execute
        def boom(action, args):
            raise RuntimeError("boom")
        self.protocol._execute = boom
        msg = {"protocol": 1, "request_id": "boom-001", "action": "create_job", "args": {"job_id": "x", "title": "X", "goal": "X"}}
        first = self.protocol.dispatch(msg)
        self.assertEqual(first["error"]["type"], "internal_error")
        self.protocol._execute = original
        second = self.protocol.dispatch(msg)
        self.assertEqual(second["error"]["type"], "indeterminate_request")

    def test_unix_socket_roundtrip_and_permissions(self):
        path = self.root / "sock" / "harness.sock"
        ready = threading.Event()
        stop = threading.Event()
        state = {}

        def serve():
            thread_store = SQLiteHarnessStore(self.root / "socket.sqlite3")
            try:
                protocol = LocalProtocol(HarnessService(thread_store))
                with HarnessUnixServer(path, protocol) as server:
                    state["server"] = server
                    ready.set()
                    server.serve_forever(poll_interval=0.05)
            finally:
                thread_store.close()
                stop.set()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(2), "server did not become ready")
        try:
            response = request({
                "protocol": 1,
                "request_id": "socket-create-001",
                "action": "create_job",
                "args": {"job_id": "socket-job", "title": "Socket", "goal": "Round trip"},
            }, path=path)
            self.assertTrue(response["ok"], response)
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, 0o600)
            continuation = request({"protocol": 1, "action": "continuation", "args": {"job_id": "socket-job"}}, path=path)
            self.assertEqual(continuation["result"]["job"]["job_id"], "socket-job")
        finally:
            state["server"].shutdown()
            thread.join(timeout=2)
        self.assertTrue(stop.is_set())
        self.assertFalse(path.exists())

    def test_runtime_namespace_is_not_shell_bridge_namespace(self):
        self.assertEqual(APP_ID, "chatgpt-conversation-harness-v1")
        self.assertNotIn("chatgpt-shell-bridge", str(state_dir()))


if __name__ == "__main__":
    unittest.main()
