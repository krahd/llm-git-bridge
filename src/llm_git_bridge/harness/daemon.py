"""Local Unix-socket server for conversation harness v1."""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import socket
import socketserver
import stat
import threading
from typing import Any

from .local_protocol import LocalProtocol, MAX_MESSAGE_BYTES
from .browser_api import BrowserAPIServer, DEFAULT_HOST, DEFAULT_PORT
from .runtime import database_path, socket_path, state_dir
from .service import HarnessService
from .sqlite_store import SQLiteHarnessStore


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline(MAX_MESSAGE_BYTES + 1)
        if not raw or len(raw) > MAX_MESSAGE_BYTES or not raw.endswith(b"\n"):
            response = {"ok": False, "protocol": 1, "request_id": None, "error": {"type": "validation_error", "message": "invalid message framing"}}
        else:
            try:
                message = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                response = {"ok": False, "protocol": 1, "request_id": None, "error": {"type": "validation_error", "message": "invalid JSON"}}
            else:
                response = self.server.protocol.dispatch(message)  # type: ignore[attr-defined]
        encoded = (json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        self.wfile.write(encoded)


class HarnessUnixServer(socketserver.UnixStreamServer):
    allow_reuse_address = False

    def __init__(self, path: str | Path, protocol: LocalProtocol):
        self.socket_path = Path(path)
        self.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.socket_path.parent, 0o700)
        except OSError:
            pass
        self._prepare_socket_path()
        super().__init__(str(self.socket_path), _Handler)
        self.protocol = protocol
        os.chmod(self.socket_path, 0o600)
        created = self.socket_path.lstat()
        self._socket_identity = (created.st_dev, created.st_ino)

    def _prepare_socket_path(self) -> None:
        try:
            existing = self.socket_path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(existing.st_mode):
            raise RuntimeError(f"socket path exists and is not a socket: {self.socket_path}")

        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.25)
        try:
            probe.connect(str(self.socket_path))
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return
            if exc.errno != errno.ECONNREFUSED:
                raise RuntimeError(f"cannot verify existing socket path: {self.socket_path}") from exc
            try:
                current = self.socket_path.lstat()
            except FileNotFoundError:
                return
            if (
                not stat.S_ISSOCK(current.st_mode)
                or (current.st_dev, current.st_ino) != (existing.st_dev, existing.st_ino)
            ):
                raise RuntimeError(f"socket path changed during stale-socket check: {self.socket_path}")
            self.socket_path.unlink()
        else:
            raise RuntimeError(f"socket already active: {self.socket_path}")
        finally:
            probe.close()

    def server_close(self) -> None:
        super().server_close()
        identity = getattr(self, "_socket_identity", None)
        if identity is None:
            return
        try:
            current = self.socket_path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISSOCK(current.st_mode) and (current.st_dev, current.st_ino) == identity:
            self.socket_path.unlink()


def request(message: dict[str, Any], *, path: str | Path | None = None, timeout: float = 5.0) -> dict[str, Any]:
    target = Path(path) if path is not None else socket_path()
    data = (json.dumps(message, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(data) > MAX_MESSAGE_BYTES:
        raise ValueError("message too large")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(target))
        sock.sendall(data)
        chunks = bytearray()
        while True:
            block = sock.recv(65536)
            if not block:
                break
            chunks.extend(block)
            if len(chunks) > MAX_MESSAGE_BYTES:
                raise RuntimeError("response too large")
            if chunks.endswith(b"\n"):
                break
    return json.loads(bytes(chunks).decode("utf-8"))


def serve_forever(*, db: str | Path | None = None, sock: str | Path | None = None, http_host: str = DEFAULT_HOST, http_port: int = DEFAULT_PORT) -> None:
    root = state_dir()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    db_file = Path(db) if db else database_path()
    store = SQLiteHarnessStore(db_file)
    http = BrowserAPIServer((http_host, int(http_port)), db_file)
    http_thread = threading.Thread(target=http.serve_forever, kwargs={"poll_interval":0.25}, daemon=True)
    http_thread.start()
    try:
        protocol = LocalProtocol(HarnessService(store))
        with HarnessUnixServer(Path(sock) if sock else socket_path(), protocol) as server:
            server.serve_forever(poll_interval=0.5)
    finally:
        http.shutdown(); http.server_close(); http_thread.join(timeout=2)
        store.close()
