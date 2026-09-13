from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import BridgeError, atomic_write_text, run


class TransientTransportError(BridgeError):
    """A transport read failed before request bytes were available safely.

    Callers must leave the remote request pending and retry later rather than
    publishing a terminal application-level result for bytes they never read.
    """


class _RcloneRCUnavailable(BridgeError):
    """The RC socket could not be connected, so no operation was submitted."""


@dataclass(frozen=True)
class RemoteFileEntry:
    name: str
    size: int | None


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
        try:
            conn.connect()
        except OSError as exc:
            raise _RcloneRCUnavailable("rclone rc socket is unavailable") from exc
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


def _retire_existing_rcd(socket_path: Path, *, timeout: float = 2.0) -> None:
    """Stop an app-private RC server left behind by an earlier watcher.

    Watchers are serialized by the application lock, so a healthy process on this
    app-specific socket is stale/orphaned. Replacing it guarantees that a daemon
    restart reloads the current rclone configuration and OAuth credentials.
    """
    if not _rc_ready(socket_path):
        socket_path.unlink(missing_ok=True)
        return
    try:
        _rc_request(socket_path, "core/quit", {}, timeout=min(1.0, timeout))
    except BridgeError:
        pass
    deadline = time.monotonic() + max(0.25, timeout)
    while time.monotonic() < deadline:
        if not _rc_ready(socket_path, timeout=0.1):
            break
        time.sleep(0.05)
    socket_path.unlink(missing_ok=True)


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

    # Always own the RC child for this watcher. A previous daemon may have been
    # killed without reaping rclone; reusing that process would also keep stale
    # OAuth/backend state after a configuration migration.
    _retire_existing_rcd(socket_path)
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
        control_upload_timeout: float = 10,
        rc_control_upload_timeout: float = 6,
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
        self.control_upload_timeout = max(1.0, min(float(control_upload_timeout), float(timeout)))
        self.rc_control_upload_timeout = max(0.5, min(float(rc_control_upload_timeout), self.control_upload_timeout, float(timeout)))
        self.mkdir_timeout = max(1.0, min(float(mkdir_timeout), float(timeout)))
        self.delete_timeout = max(1.0, min(float(delete_timeout), float(timeout)))
        self.rc_delete_timeout = max(0.5, min(float(rc_delete_timeout), self.delete_timeout, float(timeout)))
        self.rc_socket = rc_socket.expanduser() if rc_socket is not None else None
        self.last_mode = "subprocess"

    def _remote(self, rel: str) -> str:
        rel = rel.lstrip("/")
        return f"{self.remote}:{rel}"

    def _rc(
        self,
        command: str,
        payload: dict[str, Any],
        *,
        timeout: float,
        fallback_on_error: bool = True,
    ) -> dict[str, Any] | None:
        if self.rc_socket is None or not self.rc_socket.exists():
            return None
        try:
            obj = _rc_request(self.rc_socket, command, payload, timeout=timeout)
        except _RcloneRCUnavailable:
            # Connection was never established, so the operation was not submitted
            # and a subprocess fallback is safe even for a mutating call.
            self.last_mode = "subprocess"
            return None
        except BridgeError:
            self.last_mode = "subprocess"
            if fallback_on_error:
                return None
            # For mutating RC calls a timeout is an ambiguous outcome: rclone may
            # still complete the operation after our HTTP client gives up. Running
            # an immediate subprocess fallback can therefore create duplicate Drive
            # objects. Fail this attempt and let the daemon retry after RC recovery.
            raise BridgeError("rclone rc write outcome is unknown; retry required")
        self.last_mode = "rcd"
        return obj

    def ensure_dir(self, rel: str) -> None:
        rel = rel.lstrip("/")
        if self._rc(
            "operations/mkdir",
            {"fs": f"{self.remote}:", "remote": rel},
            timeout=self.mkdir_timeout,
            fallback_on_error=False,
        ) is not None:
            return
        self.last_mode = "subprocess"
        run(["rclone", "mkdir", self._remote(rel)], timeout=self.mkdir_timeout)

    @staticmethod
    def _entry_from_mapping(item: dict[str, Any]) -> RemoteFileEntry | None:
        if item.get("IsDir"):
            return None
        name = item.get("Name")
        if not isinstance(name, str) or not name:
            path = item.get("Path")
            if isinstance(path, str) and path:
                name = Path(path).name
        if not isinstance(name, str) or not name:
            return None
        size = item.get("Size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            size = None
        return RemoteFileEntry(name=name, size=size)

    @staticmethod
    def _validate_entries(entries: list[RemoteFileEntry]) -> list[RemoteFileEntry]:
        names = [entry.name for entry in entries]
        if len(names) != len(set(names)):
            raise BridgeError("duplicate filenames in remote directory")
        return sorted(entries, key=lambda entry: entry.name)

    def list_entries(self, rel: str) -> list[RemoteFileEntry]:
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
            entries: list[RemoteFileEntry] = []
            for item in listing:
                if not isinstance(item, dict):
                    continue
                entry = self._entry_from_mapping(item)
                if entry is not None:
                    entries.append(entry)
            return self._validate_entries(entries)

        self.last_mode = "subprocess"
        proc = run(
            ["rclone", "lsjson", self._remote(rel), "--files-only", "--max-depth", "1"],
            check=False,
            timeout=self.list_timeout,
        )
        if proc.returncode != 0:
            raise BridgeError(f"rclone list failed for {rel!r}")
        try:
            listing = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise BridgeError("rclone list returned malformed JSON") from exc
        if not isinstance(listing, list):
            raise BridgeError("rclone list returned malformed JSON")
        entries = []
        for item in listing:
            if not isinstance(item, dict):
                continue
            entry = self._entry_from_mapping(item)
            if entry is not None:
                entries.append(entry)
        return self._validate_entries(entries)

    def list_files(self, rel: str) -> list[str]:
        return [entry.name for entry in self.list_entries(rel)]

    def download_text(self, rel: str, local: Path, *, max_bytes: int | None = None) -> str:
        local.parent.mkdir(parents=True, exist_ok=True)
        rel = rel.lstrip("/")
        rc_tmp = local.with_name(local.name + ".rc-part")
        subprocess_tmp = local.with_name(local.name + ".subprocess-part")
        local.unlink(missing_ok=True)
        rc_tmp.unlink(missing_ok=True)
        subprocess_tmp.unlink(missing_ok=True)

        try:
            try:
                rc_obj = self._rc(
                    "operations/copyfile",
                    {
                        "srcFs": f"{self.remote}:",
                        "srcRemote": rel,
                        "dstFs": str(rc_tmp.parent),
                        "dstRemote": rc_tmp.name,
                    },
                    timeout=self.rc_download_timeout,
                )
                completed = rc_tmp if rc_obj is not None else subprocess_tmp
                if rc_obj is None:
                    self.last_mode = "subprocess"
                    run(["rclone", "copyto", self._remote(rel), str(subprocess_tmp)], timeout=self.download_timeout)
            except BridgeError as exc:
                raise TransientTransportError("transaction download failed; retry required") from exc

            try:
                size = completed.stat().st_size
            except OSError as exc:
                raise TransientTransportError("downloaded request is not yet available; retry required") from exc
            if max_bytes is not None and size > max_bytes:
                raise BridgeError("remote request exceeds maximum allowed size")
            try:
                os.replace(completed, local)
            except OSError as exc:
                raise TransientTransportError("downloaded request cannot be stored safely; retry required") from exc
            try:
                return local.read_text(encoding="utf-8-sig")
            except UnicodeError as exc:
                raise BridgeError("remote request is not valid UTF-8 JSON text") from exc
            except OSError as exc:
                raise TransientTransportError("downloaded request cannot be read safely; retry required") from exc
        finally:
            # If an RC request timed out it may still be writing rc_tmp. Unlinking
            # the pathname is safe on POSIX and prevents a later poll from consuming
            # stale partial bytes; an already-open writer can finish only to its
            # now-unlinked inode.
            rc_tmp.unlink(missing_ok=True)
            subprocess_tmp.unlink(missing_ok=True)

    def upload_text(
        self,
        rel: str,
        text: str,
        local_tmp: Path,
        *,
        timeout: float | None = None,
        rc_timeout: float | None = None,
    ) -> None:
        atomic_write_text(local_tmp, text)
        rel = rel.lstrip("/")
        upload_timeout = self.timeout if timeout is None else max(1.0, min(float(timeout), float(self.timeout)))
        upload_rc_timeout = upload_timeout if rc_timeout is None else max(0.5, min(float(rc_timeout), upload_timeout))
        if self._rc(
            "operations/copyfile",
            {
                "srcFs": str(local_tmp.parent),
                "srcRemote": local_tmp.name,
                "dstFs": f"{self.remote}:",
                "dstRemote": rel,
            },
            timeout=upload_rc_timeout,
            fallback_on_error=False,
        ) is None:
            self.last_mode = "subprocess"
            run(["rclone", "copyto", str(local_tmp), self._remote(rel)], timeout=upload_timeout)

    def delete_file(self, rel: str, *, allow_fallback: bool = True) -> bool:
        rel = rel.lstrip("/")
        rc_available = self.rc_socket is not None and self.rc_socket.exists()
        try:
            rc_obj = self._rc(
                "operations/deletefile",
                {"fs": f"{self.remote}:", "remote": rel},
                timeout=self.rc_delete_timeout,
                fallback_on_error=False,
            )
        except BridgeError:
            # Deletion is maintenance only. An RC timeout may already have deleted
            # the object, so do not race it with a second mutating request.
            return False
        if rc_obj is not None:
            return True
        if rc_available or not allow_fallback:
            return False
        self.last_mode = "subprocess"
        proc = run(["rclone", "deletefile", self._remote(rel)], check=False, timeout=self.delete_timeout)
        if proc.returncode != 0:
            raise BridgeError(f"rclone delete failed for {rel!r}")
        return True

    def upload_json(
        self,
        rel: str,
        obj: Any,
        local_tmp: Path,
        *,
        timeout: float | None = None,
        rc_timeout: float | None = None,
    ) -> None:
        self.upload_text(
            rel,
            json.dumps(obj, indent=2, sort_keys=True) + "\n",
            local_tmp,
            timeout=timeout,
            rc_timeout=rc_timeout,
        )

    def upload_control_json(self, rel: str, obj: Any, local_tmp: Path) -> None:
        self.upload_json(
            rel,
            obj,
            local_tmp,
            timeout=self.control_upload_timeout,
            rc_timeout=self.rc_control_upload_timeout,
        )
