from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core import BridgeError, atomic_write_text, run


class RcloneTransport:
    def __init__(self, remote: str, *, timeout: float = 60):
        remote = remote.rstrip(":")
        if not remote:
            raise BridgeError("empty rclone remote")
        self.remote = remote
        self.timeout = timeout

    def _remote(self, rel: str) -> str:
        rel = rel.lstrip("/")
        return f"{self.remote}:{rel}"

    def ensure_dir(self, rel: str) -> None:
        run(["rclone", "mkdir", self._remote(rel)], timeout=self.timeout)

    def list_files(self, rel: str) -> list[str]:
        proc = run(["rclone", "lsf", self._remote(rel), "--files-only"], check=False, timeout=self.timeout)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise BridgeError(f"rclone list failed for {rel!r}: {detail or 'unknown error'}")
        return sorted(line.strip() for line in proc.stdout.splitlines() if line.strip())

    def download_text(self, rel: str, local: Path) -> str:
        local.parent.mkdir(parents=True, exist_ok=True)
        run(["rclone", "copyto", self._remote(rel), str(local)], timeout=self.timeout)
        return local.read_text(encoding="utf-8-sig")

    def upload_text(self, rel: str, text: str, local_tmp: Path) -> None:
        atomic_write_text(local_tmp, text)
        run(["rclone", "copyto", str(local_tmp), self._remote(rel)], timeout=self.timeout)

    def upload_json(self, rel: str, obj: Any, local_tmp: Path) -> None:
        self.upload_text(rel, json.dumps(obj, indent=2, sort_keys=True) + "\n", local_tmp)
