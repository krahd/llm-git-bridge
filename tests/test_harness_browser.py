from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from llm_git_bridge.harness.browser_api import BrowserAPIServer
from llm_git_bridge.harness.service import HarnessService
from llm_git_bridge.harness.sqlite_store import SQLiteHarnessStore

ROOT=Path(__file__).resolve().parents[1]
MANIFEST=ROOT/"safari"/"extension"/"manifest.json"
BACKGROUND=ROOT/"safari"/"extension"/"background.js"
POPUP=ROOT/"safari"/"extension"/"popup.js"
CONTENT=ROOT/"safari"/"extension"/"content.js"

class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.db=Path(self.tmp.name)/"h.sqlite3"
        store=SQLiteHarnessStore(self.db); self.service=HarnessService(store); self.store=store
        self.service.create_job("job-a",title="A",goal="Continue A",repo_path="/secret/path",next_action="write section")
        lease=self.service.acquire_lease("job-a","conversation-a")
        self.handoff=self.service.create_handoff(lease,ttl=300)
        self.server=BrowserAPIServer(("127.0.0.1",0),self.db)
        self.port=self.server.server_address[1]
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.origin="safari-web-extension://test-extension"
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2); self.store.close(); self.tmp.cleanup()
    def _req(self,path,*,method="GET",body=None,token=None,origin=True):
        data=None if body is None else json.dumps(body).encode()
        headers={}
        if origin: headers["Origin"]=self.origin
        if data is not None: headers["Content-Type"]="application/json"
        if token: headers["Authorization"]="Bearer "+token
        req=urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",data=data,headers=headers,method=method)
        try:
            with urllib.request.urlopen(req,timeout=2) as r: return r.status,json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code,json.loads(e.read())
    def test_pairing_is_one_time_and_pending_prompt_is_private_projection(self):
        pairing=self.service.create_pairing_code(ttl=300)["code"]
        status,body=self._req("/pair",method="POST",body={"code":pairing,"client_id":"safari-test-client"})
        self.assertEqual(status,200); token=body["token"]
        status,replay=self._req("/pair",method="POST",body={"code":pairing,"client_id":"safari-test-client2"})
        self.assertEqual(status,403)
        status,pending=self._req("/v1/pending",token=token)
        self.assertEqual(status,200); self.assertEqual(len(pending["pending"]),1)
        item=pending["pending"][0]
        self.assertEqual(item["job_id"],"job-a")
        self.assertNotIn("prompt",item)
        self.assertNotIn(self.handoff.nonce,json.dumps(pending))
        self.assertNotIn("/secret/path",json.dumps(pending))
        status,opened=self._req("/v1/open-next",method="POST",body={},token=token)
        self.assertEqual(status,200,opened)
        reserved=opened["item"]
        self.assertEqual(reserved["handoff_id"],self.handoff.handoff_id)
        self.assertIn(self.handoff.nonce,reserved["prompt"])
        self.assertIn("ChatGPT Shell Bridge",reserved["prompt"])
        self.assertIn("parallel Conversation Harness v1",reserved["prompt"])
    def test_pending_requires_extension_origin_and_token(self):
        status,_=self._req("/v1/pending")
        self.assertEqual(status,401)
        pairing=self.service.create_pairing_code(ttl=300)["code"]
        _,body=self._req("/pair",method="POST",body={"code":pairing,"client_id":"safari-test-client"})
        status,_=self._req("/v1/pending",token=body["token"],origin=False)
        self.assertEqual(status,403)
    def test_claim_removes_pending_secret(self):
        pairing=self.service.create_pairing_code(ttl=300)["code"]
        _,body=self._req("/pair",method="POST",body={"code":pairing,"client_id":"safari-test-client"})
        self.service.claim_handoff("job-a",self.handoff.nonce,"conversation-b")
        _,pending=self._req("/v1/pending",token=body["token"])
        self.assertEqual(pending["pending"],[])
        row=self.store.conn.execute("SELECT nonce_secret FROM handoffs WHERE handoff_id=?",(self.handoff.handoff_id,)).fetchone()
        self.assertIsNone(row[0])
    def test_manifest_is_narrow(self):
        manifest=json.loads(MANIFEST.read_text())
        self.assertNotIn("<all_urls>",json.dumps(manifest))
        self.assertEqual(set(manifest["host_permissions"]),{"https://chatgpt.com/*","http://127.0.0.1:47653/*"})
        self.assertNotIn("nativeMessaging",manifest["permissions"])
        self.assertNotIn("activeTab",manifest["permissions"])
    def test_background_persists_pending_prompt_across_mv3_worker_restarts(self):
        background=BACKGROUND.read_text()
        self.assertNotIn("new Map",background)
        self.assertIn("browser.storage.local.set",background)
        self.assertIn("browser.storage.local.get",background)
        self.assertIn("browser.storage.local.remove",background)
        self.assertIn("pendingPrompt:",background)
    def test_background_expires_persisted_handoff_prompt(self):
        background=BACKGROUND.read_text()
        self.assertIn("PENDING_TTL_MS",background)
        self.assertIn("createdAt: Date.now()",background)
        self.assertIn("age > PENDING_TTL_MS",background)
        self.assertIn("age < 0",background)
        self.assertIn("browser.storage.local.remove(key)",background)

    def test_content_script_waits_for_async_composer_and_wakes_background(self):
        content=CONTENT.read_text()
        background=BACKGROUND.read_text()
        self.assertIn("MutationObserver",content)
        self.assertIn("timeoutMs = 15000",content)
        self.assertIn('type: "contentReady"',content)
        self.assertIn('message?.type === "contentReady"',background)
        self.assertIn("deliverPending(sender.tab.id)",background)
    def test_popup_reports_readiness_and_clears_stale_browser_token(self):
        popup=POPUP.read_text()
        self.assertIn('fetch(BASE + "/health")',popup)
        self.assertIn('browser.storage.local.remove("token")',popup)
        self.assertIn("Harness ready. Pair this browser.",popup)
        self.assertIn("pending handoff",popup)


    def _pair_browser(self, client_id):
        pairing=self.service.create_pairing_code(ttl=300)["code"]
        status,body=self._req("/pair",method="POST",body={"code":pairing,"client_id":client_id})
        self.assertEqual(status,200,body)
        return body["token"]

    def test_open_next_reserves_handoff_and_release_recycles_it(self):
        token_a=self._pair_browser("safari-browser-a")
        status,opened=self._req("/v1/open-next",method="POST",body={},token=token_a)
        self.assertEqual(status,200,opened)
        first=opened["item"]
        self.assertEqual(first["handoff_id"],self.handoff.handoff_id)
        self.assertTrue(first["new_reservation"])

        status,repeated=self._req("/v1/open-next",method="POST",body={},token=token_a)
        self.assertEqual(status,200,repeated)
        self.assertEqual(repeated["item"]["handoff_id"],self.handoff.handoff_id)
        self.assertFalse(repeated["item"]["new_reservation"])

        status,pending=self._req("/v1/pending",token=token_a)
        self.assertEqual(status,200,pending)
        self.assertEqual(pending["pending"],[])

        token_b=self._pair_browser("safari-browser-b")
        status,blocked=self._req("/v1/open-next",method="POST",body={},token=token_b)
        self.assertEqual(status,200,blocked)
        self.assertIsNone(blocked["item"])

        status,released=self._req(
            "/v1/release-start",method="POST",
            body={"handoff_id":self.handoff.handoff_id},token=token_a,
        )
        self.assertEqual(status,200,released)
        self.assertTrue(released["released"])

        status,reopened=self._req("/v1/open-next",method="POST",body={},token=token_b)
        self.assertEqual(status,200,reopened)
        self.assertEqual(reopened["item"]["handoff_id"],self.handoff.handoff_id)
        self.assertTrue(reopened["item"]["new_reservation"])

    def test_claim_from_browser_starting_reservation_clears_reservation_fields(self):
        token=self._pair_browser("safari-browser-a")
        status,opened=self._req("/v1/open-next",method="POST",body={},token=token)
        self.assertEqual(status,200,opened)
        self.assertEqual(opened["item"]["handoff_id"],self.handoff.handoff_id)
        lease=self.service.claim_handoff("job-a",self.handoff.nonce,"conversation-b")
        self.assertGreater(lease.generation,1)
        row=self.store.conn.execute(
            "SELECT state,nonce_secret,starting_by,starting_at,starting_expires_at FROM handoffs WHERE handoff_id=?",
            (self.handoff.handoff_id,),
        ).fetchone()
        self.assertEqual(row["state"],"claimed")
        self.assertIsNone(row["nonce_secret"])
        self.assertIsNone(row["starting_by"])
        self.assertIsNone(row["starting_at"])
        self.assertIsNone(row["starting_expires_at"])

if __name__=="__main__": unittest.main()
