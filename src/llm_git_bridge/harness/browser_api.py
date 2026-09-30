"""Authenticated loopback HTTP API for browser-extension re-entry."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .errors import HarnessError
from .service import HarnessService
from .sqlite_store import SQLiteHarnessStore

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 47653
MAX_BODY = 64 * 1024
ALLOWED_ORIGIN_PREFIXES = ("safari-web-extension://", "chrome-extension://", "moz-extension://")


class BrowserAPIServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], db_path: str | Path):
        host, _ = address
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("browser API must bind to loopback")
        self.db_path = Path(db_path)
        super().__init__(address, BrowserAPIHandler)


class BrowserAPIHandler(BaseHTTPRequestHandler):
    server_version = "ConversationHarness/1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _origin(self) -> str | None:
        origin = self.headers.get("Origin")
        if origin and any(origin.startswith(prefix) for prefix in ALLOWED_ORIGIN_PREFIXES):
            return origin
        return None

    def _cors(self) -> None:
        origin = self._origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        data=(json.dumps(payload,sort_keys=True,separators=(",",":"))+"\n").encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(data)))
        self.send_header("Cache-Control","no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict[str, Any]:
        try:
            length=int(self.headers.get("Content-Length","0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if length < 0 or length > MAX_BODY:
            raise ValueError("request body too large")
        raw=self.rfile.read(length)
        obj=json.loads(raw.decode("utf-8"))
        if not isinstance(obj,dict):
            raise ValueError("JSON body must be an object")
        return obj

    def _service(self) -> tuple[SQLiteHarnessStore,HarnessService]:
        store=SQLiteHarnessStore(self.server.db_path)  # type: ignore[attr-defined]
        return store,HarnessService(store)

    def _bearer(self) -> str | None:
        value=self.headers.get("Authorization","")
        return value[7:] if value.startswith("Bearer ") else None

    @staticmethod
    def _pending_payload(service: HarnessService, client_id: str) -> dict[str, Any]:
        pending = []
        for item in service.pending_reentries():
            pending.append({k: v for k, v in item.items() if k != "prompt"})
        return {"ok":True,"client_id":client_id,"pending":pending}

    def do_OPTIONS(self) -> None:
        if self._origin() is None:
            self._send(403,{"ok":False,"error":"extension origin required"}); return
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Headers","Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods","GET, POST, OPTIONS")
        self.send_header("Access-Control-Max-Age","600")
        self.end_headers()

    def do_GET(self) -> None:
        parsed=urlparse(self.path)
        if parsed.path == "/health":
            self._send(200,{"ok":True,"service":"chatgpt-conversation-harness-v1","protocol":1}); return
        if parsed.path != "/v1/pending":
            self._send(404,{"ok":False,"error":"not found"}); return
        if self._origin() is None:
            self._send(403,{"ok":False,"error":"extension origin required"}); return
        token=self._bearer()
        if not token:
            self._send(401,{"ok":False,"error":"bearer token required"}); return
        store,service=self._service()
        try:
            client_id=service.authenticate_browser(token)
            self._send(200,self._pending_payload(service,client_id))
        except HarnessError as exc:
            self._send(401,{"ok":False,"error":str(exc)})
        finally:
            store.close()

    def do_POST(self) -> None:
        parsed=urlparse(self.path)
        if parsed.path not in {"/pair", "/v1/pending", "/v1/open-next", "/v1/release-start"}:
            self._send(404,{"ok":False,"error":"not found"}); return
        if self._origin() is None:
            self._send(403,{"ok":False,"error":"extension origin required"}); return
        try:
            body=self._body()
        except (ValueError,UnicodeError,json.JSONDecodeError) as exc:
            self._send(400,{"ok":False,"error":str(exc)}); return
        store,service=self._service()
        try:
            if parsed.path == "/pair":
                try:
                    result=service.consume_pairing_code(body.get("code"),body.get("client_id"))
                except HarnessError as exc:
                    self._send(403,{"ok":False,"error":str(exc)}); return
                self._send(200,{"ok":True,**result}); return
            token=self._bearer() or body.get("token")
            if not token:
                self._send(401,{"ok":False,"error":"browser token required"}); return
            try:
                client_id=service.authenticate_browser(token)
            except HarnessError as exc:
                self._send(401,{"ok":False,"error":str(exc)}); return
            try:
                if parsed.path == "/v1/pending":
                    self._send(200,self._pending_payload(service,client_id)); return
                if parsed.path == "/v1/open-next":
                    item=service.reserve_reentry(client_id,ttl=body.get("ttl",300.0))
                    self._send(200,{"ok":True,"client_id":client_id,"item":item}); return
                result=service.release_reentry(client_id,body.get("handoff_id"))
                self._send(200,{"ok":True,"client_id":client_id,**result})
            except HarnessError as exc:
                self._send(409,{"ok":False,"error":str(exc)})
        finally:
            store.close()

