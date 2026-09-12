from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from .core import BridgeError, atomic_write_text, run


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: Path, *, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = str(socket_path)

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.socket_path)
        self.sock = sock


def _rc_request(socket_path: Path, command: str, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    conn = _UnixHTTPConnection(socket_path, timeout=timeout)
    try:
        body = json.dumps(payload).encode("utf-8")
        conn.request(
            "POST",
            "/" + command.lstrip("/"),
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        response = conn.getresponse()
        raw = response.read().decode("utf-8", errors="replace")
        if response.status >= 400:
            detail = raw.strip() or response.reason
            raise BridgeError(f"rclone rc {command} failed ({response.status}): {detail}")
        if not raw.strip():
            return {}
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise BridgeError(f"rclone rc {command} returned a non-object response")
        return obj
    except BridgeError:
        raise
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
        raise BridgeError(f"rclone rc {command} failed: {exc}") from exc
    finally:
        conn.close()


class RcloneRCProcess:
    def __init__(self, socket_path: Path, process: subprocess.Popen[bytes] | None):
        self.socket_path = socket_path
        self.process = process

    @property
    def owned(self) -> bool:
        return self.process is not None

    def healthy(self, *, timeout: float = 0.25) -> bool:
        if self.process is not None and self.process.poll() is not None:
            return False
        return _rc_ready(self.socket_path, timeout=timeout)

    def stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.socket_path.unlink(missing_ok=True)


def _rc_ready(socket_path: Path, *, timeout: float = 0.75) -> bool:
    if not socket_path.exists():
        return False
    try:
        _rc_request(socket_path, "rc/noop", {}, timeout=timeout)
        return True
    except BridgeError:
        return False


def start_rclone_rcd(socket_path: Path, *, startup_timeout: float = 5.0) -> RcloneRCProcess | None:
    """Start a private persistent rclone RC server, or reuse an existing one.

    The socket lives inside a user-private state directory. If rcd cannot be
    started, callers can continue with the ordinary rclone subprocess transport.
    """
    rclone = shutil.which("rclone")
    if not rclone:
        return None

    socket_path = socket_path.expanduser()
    socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(socket_path.parent, 0o700)
    except OSError:
        pass

    if _rc_ready(socket_path):
        return RcloneRCProcess(socket_path, None)

    # A crashed rcd can leave a stale unix socket behind.
    socket_path.unlink(missing_ok=True)
    log_path = socket_path.parent / "rclone-rcd.log"
    log_fh = log_path.open("ab", buffering=0)
    try:
        process = subprocess.Popen(
            [
                rclone,
                "rcd",
                "--rc-addr",
                f"unix://{socket_path}",
                "--rc-no-auth",
            ],
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )
    finally:
        log_fh.close()

    deadline = time.monotonic() + max(0.5, float(startup_timeout))
    while time.monotonic() < deadline:
        if socket_path.exists():
            try:
                os.chmod(socket_path, 0o600)
            except OSError:
                pass
            if _rc_ready(socket_path):
                return RcloneRCProcess(socket_path, process)
        if process.poll() is not None:
            break
        time.sleep(0.05)

    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    socket_path.unlink(missing_ok=True)
    return None


class RcloneTransport:
    def __init__(
        self,
        remote: str,
        *,
        timeout: float = 60,
        list_timeout: float = 5,
        rc_list_timeout: float = 3,
        download_timeout: float = 8,
        rc_download_timeout: float = 5,
        mkdir_timeout: float = 12,
        delete_timeout: float = 3,
        rc_delete_timeout: float = 2,
        rc_socket: Path | None = None,
    ):
        remote = remote.rstrip(":")
        if not remote:
            raise BridgeError("empty rclone remote")
        self.remote = remote
        self.timeout = timeout
        self.list_timeout = max(1.0, min(float(list_timeout), float(timeout)))
        self.rc_list_timeout = max(0.5, min(float(rc_list_timeout), self.list_timeout, float(timeout)))
        self.download_timeout = max(1.0, min(float(download_timeout), float(timeout)))
        self.rc_download_timeout = max(0.5, min(float(rc_download_timeout), self.download_timeout, float(timeout)))
        self.mkdir_timeout = max(1.0, min(float(mkdir_timeout), float(timeout)))
        self.delete_timeout = max(1.0, min(float(delete_timeout), float(timeout)))
        self.rc_delete_timeout = max(0.5, min(float(rc_delete_timeout), self.delete_timeout, float(timeout)))
        self.rc_socket = rc_socket.expanduser() if rc_socket is not None else None
        self.last_mode = "subprocess"

    def _remote(self, rel: str) -> str:
        rel = rel.lstrip("/")
        return f"{self.remote}:{rel}"

    def _rc(self, command: str, payload: dict[str, Any], *, timeout: float) -> dict[str, Any] | None:
        if self.rc_socket is None or not self.rc_socket.exists():
            return None
        try:
            obj = _rc_request(self.rc_socket, command, payload, timeout=timeout)
        except BridgeError:
            # RC is an optimisation only. The established subprocess path remains
            # the correctness fallback if rcd is unavailable or unhealthy.
            self.last_mode = "subprocess"
            return None
        self.last_mode = "rcd"
        return obj

    def ensure_dir(self, rel: str) -> None:
        rel = rel.lstrip("/")
        if self._rc(
            "operations/mkdir",
            {"fs": f"{self.remote}:", "remote": rel},
            timeout=self.mkdir_timeout,
        ) is not None:
            return
        self.last_mode = "subprocess"
        run(["rclone", "mkdir", self._remote(rel)], timeout=self.mkdir_timeout)

    def list_files(self, rel: str) -> list[str]:
        rel = rel.lstrip("/")
        obj = self._rc(
            "operations/list",
            {"fs": f"{self.remote}:", "remote": rel, "opt": {"filesOnly": True}},
            timeout=self.rc_list_timeout,
        )
        if obj is not None:
            listing = obj.get("list", [])
            if not isinstance(listing, list):
                raise BridgeError(f"rclone rc list failed for {rel!r}: malformed response")
            names: list[str] = []
            for item in listing:
                if not isinstance(item, dict) or item.get("IsDir"):
                    continue
                name = item.get("Name")
                if not isinstance(name, str) or not name:
                    path = item.get("Path")
                    if isinstance(path, str) and path:
                        name = Path(path).name
                if isinstance(name, str) and name:
                    names.append(name)
            return sorted(names)

        self.last_mode = "subprocess"
        proc = run(["rclone", "lsf", self._remote(rel), "--files-only"], check=False, timeout=self.list_timeout)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise BridgeError(f"rclone list failed for {rel!r}: {detail or 'unknown error'}")
        return sorted(line.strip() for line in proc.stdout.splitlines() if line.strip())

    def download_text(self, rel: str, local: Path) -> str:
        local.parent.mkdir(parents=True, exist_ok=True)
        rel = rel.lstrip("/")
        if self._rc(
            "operations/copyfile",
            {
                "srcFs": f"{self.remote}:",
                "srcRemote": rel,
                "dstFs": str(local.parent),
                "dstRemote": local.name,
            },
            timeout=self.rc_download_timeout,
        ) is None:
            self.last_mode = "subprocess"
            run(["rclone", "copyto", self._remote(rel), str(local)], timeout=self.download_timeout)
        return local.read_text(encoding="utf-8-sig")

    def upload_text(self, rel: str, text: str, local_tmp: Path) -> None:
        atomic_write_text(local_tmp, text)
        rel = rel.lstrip("/")
        if self._rc(
            "operations/copyfile",
            {
                "srcFs": str(local_tmp.parent),
                "srcRemote": local_tmp.name,
                "dstFs": f"{self.remote}:",
                "dstRemote": rel,
            },
            timeout=self.timeout,
        ) is None:
            self.last_mode = "subprocess"
            run(["rclone", "copyto", str(local_tmp), self._remote(rel)], timeout=self.timeout)

    def delete_file(self, rel: str, *, allow_fallback: bool = True) -> bool:
        rel = rel.lstrip("/")
        if self._rc(
            "operations/deletefile",
            {"fs": f"{self.remote}:", "remote": rel},
            timeout=self.rc_delete_timeout,
        ) is not None:
            return True
        if not allow_fallback:
            return False
        self.last_mode = "subprocess"
        proc = run(["rclone", "deletefile", self._remote(rel)], check=False, timeout=self.delete_timeout)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise BridgeError(f"rclone delete failed for {rel!r}: {detail or 'unknown error'}")
        return True

    def upload_json(self, rel: str, obj: Any, local_tmp: Path) -> None:
        self.upload_text(rel, json.dumps(obj, indent=2, sort_keys=True) + "\n", local_tmp)
