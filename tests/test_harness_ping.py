import tempfile
import unittest
from pathlib import Path
from llm_git_bridge.harness.local_protocol import LocalProtocol
from llm_git_bridge.harness.service import HarnessService
from llm_git_bridge.harness.sqlite_store import SQLiteHarnessStore

class PingTests(unittest.TestCase):
    def test_ping_is_read_only_and_needs_no_request_id(self):
        with tempfile.TemporaryDirectory() as td:
            store=SQLiteHarnessStore(Path(td)/"h.sqlite3")
            try:
                p=LocalProtocol(HarnessService(store))
                response=p.dispatch({"protocol":1,"action":"ping","args":{}})
                self.assertTrue(response["ok"])
                self.assertEqual(response["result"]["service"],"chatgpt-conversation-harness-v1")
                count=store.conn.execute("SELECT count(*) FROM protocol_requests").fetchone()[0]
                self.assertEqual(count,0)
            finally:
                store.close()

if __name__ == "__main__": unittest.main()
