#!/usr/bin/env python3
"""Operator-pinned, read-only Gemini Google Drive diagnostic helper.

This file is inert until an operator installs it outside the agent-writable
root, pins its SHA-256 in v6's trusted_operations config, and approves each
call locally. It never accepts a remote, path, command, or token from an agent.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time


class DrivePolicyError(ValueError):
    pass


REMOTE = "gemini-pedeciba:"
PROBE_FILE = "bridge-external-result-test.txt"
REQUEST_PATTERN = re.compile(r"gemini-bridge-request-[a-z0-9-]{1,72}\.txt\Z")
ID_PATTERN = re.compile(r"gemini-doc-poc-[a-z0-9-]{1,72}\Z")
VALUE_PATTERN = re.compile(r"[A-Z0-9-]{1,128}\Z")
MAX_POLICY_BYTES = 4096
MAX_CONFIG_BYTES = 1048576
MAX_DOC_BYTES = 8192
MAX_LIST_BYTES = 16384
MAX_REQUESTS = 12
RUN_TIMEOUT = 20


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    out = {}
    for key, value in pairs:
        if key in out:
            raise DrivePolicyError("duplicate JSON key")
        out[key] = value
    return out


def _owner_path(path: Path, *, directory: bool = False, private: bool = False) -> None:
    if not path.is_absolute() or path.is_symlink():
        raise DrivePolicyError("untrusted or nonabsolute policy path")
    try:
        meta = path.stat()
    except OSError as exc:
        raise DrivePolicyError("configured path unavailable") from exc
    if (directory and not stat.S_ISDIR(meta.st_mode)) or (not directory and not stat.S_ISREG(meta.st_mode)):
        raise DrivePolicyError("configured path has wrong type")
    if meta.st_uid != os.getuid() or meta.st_mode & (0o077 if private else 0o022):
        raise DrivePolicyError("configured path has unsafe ownership or permissions")


def _rclone_path(raw: str, digest: str) -> Path:
    if not isinstance(raw, str) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise DrivePolicyError("rclone binary and digest must be pinned")
    path = Path(raw)
    if not path.is_absolute() or path.is_symlink():
        raise DrivePolicyError("rclone executable path must be absolute and non-symlink")
    try:
        meta = path.stat()
    except OSError as exc:
        raise DrivePolicyError("rclone binary unavailable") from exc
    if (not stat.S_ISREG(meta.st_mode) or meta.st_uid not in (0, os.getuid())
            or meta.st_mode & 0o022 or not os.access(path, os.X_OK)):
        raise DrivePolicyError("rclone binary has unsafe permissions")
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            h.update(chunk)
    if h.hexdigest() != digest:
        raise DrivePolicyError("rclone binary digest changed")
    return path


def load_policy(directory: Path) -> tuple[Path, Path]:
    _owner_path(directory, directory=True, private=True)
    source = directory / "drive-policy.json"
    _owner_path(source, private=True)
    if source.stat().st_size > MAX_POLICY_BYTES:
        raise DrivePolicyError("policy too large")
    try:
        with source.open("rb") as f:
            raw = f.read(MAX_POLICY_BYTES + 1)
        obj = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (OSError, UnicodeError, ValueError) as exc:
        raise DrivePolicyError("invalid Drive policy") from exc
    if not isinstance(obj, dict) or set(obj) != {"schema", "remote", "rclone_binary", "rclone_sha256", "rclone_config"} or type(obj["schema"]) is not int or obj["schema"] != 1 or obj["remote"] != REMOTE:
        raise DrivePolicyError("policy fields or remote not pinned")
    config_raw = obj["rclone_config"]
    if not isinstance(config_raw, str):
        raise DrivePolicyError("invalid rclone config path")
    config = Path(config_raw)
    _owner_path(config, private=True)
    if config.stat().st_size > MAX_CONFIG_BYTES:
        raise DrivePolicyError("rclone config too large")
    return _rclone_path(obj["rclone_binary"], obj["rclone_sha256"]), config


def run_bounded(argv: list[str], *, max_bytes: int, timeout: int = RUN_TIMEOUT) -> bytes:
    """Bound both streams, duration and process group; do not disclose stderr."""
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True, close_fds=True)
    streams = selectors.DefaultSelector()
    out = bytearray()
    err = bytearray()
    completed = False
    deadline = time.monotonic() + timeout
    try:
        for pipe, sink in ((proc.stdout, out), (proc.stderr, err)):
            streams.register(pipe, selectors.EVENT_READ, sink)
        while streams.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DrivePolicyError("Drive read timed out")
            for event, _ in streams.select(remaining):
                chunk = os.read(event.fileobj.fileno(), 4096)
                if not chunk:
                    streams.unregister(event.fileobj)
                    event.fileobj.close()
                    continue
                sink = event.data
                limit = max_bytes if sink is out else 4096
                if len(sink) + len(chunk) > limit:
                    raise DrivePolicyError("Drive read output limit exceeded")
                sink.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DrivePolicyError("Drive read timed out")
        code = proc.wait(timeout=remaining)
        completed = True
        if code != 0:
            raise DrivePolicyError("Drive read failed; inspect locally without exposing credentials")
        return bytes(out)
    finally:
        streams.close()
        if not completed:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None and not pipe.closed:
                pipe.close()


def read_document(binary: Path, config: Path, filename: str) -> dict:
    if filename != PROBE_FILE and not REQUEST_PATTERN.fullmatch(filename):
        raise DrivePolicyError("document outside fixed test namespace")
    argv = [str(binary), "--config", str(config), "cat", REMOTE + filename,
            "--drive-export-formats", "txt"]
    raw = run_bounded(argv, max_bytes=MAX_DOC_BYTES)
    try:
        obj = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeError, ValueError) as exc:
        raise DrivePolicyError("document must contain exactly one JSON object") from exc
    if not isinstance(obj, dict):
        raise DrivePolicyError("document must contain a JSON object")
    if filename != PROBE_FILE:
        if (set(obj) != {"kind", "id", "action", "value"}
                or obj.get("kind") != "bridge_poc_request"
                or obj.get("action") != "diagnostic_echo"
                or not isinstance(obj.get("id"), str)
                or not ID_PATTERN.fullmatch(obj["id"])
                or not isinstance(obj.get("value"), str)
                or not VALUE_PATTERN.fullmatch(obj["value"])):
            raise DrivePolicyError("invalid inert diagnostic request")
    return obj


def inspect_requests(binary: Path, config: Path) -> list[dict]:
    argv = [str(binary), "--config", str(config), "lsf", REMOTE,
            "--files-only", "--max-depth", "1", "--drive-export-formats", "txt",
            "--include", "gemini-bridge-request-*.txt"]
    raw = run_bounded(argv, max_bytes=MAX_LIST_BYTES)
    try:
        names = raw.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise DrivePolicyError("invalid Drive listing") from exc
    if len(names) > MAX_REQUESTS or len(set(names)) != len(names):
        raise DrivePolicyError("too many or duplicate candidate request documents")
    if any(not REQUEST_PATTERN.fullmatch(name) for name in names):
        raise DrivePolicyError("unexpected document in request listing")
    return [{"name": name, "request": read_document(binary, config, name)} for name in sorted(names)]


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("probe-test", "inspect-requests"):
        print("usage: gemini_drive_read_helper.py ACTION POLICY_DIRECTORY", file=sys.stderr)
        return 2
    try:
        binary, config = load_policy(Path(argv[2]))
        if argv[1] == "probe-test":
            result = {"action": "probe-test", "document": PROBE_FILE,
                      "content": read_document(binary, config, PROBE_FILE)}
        else:
            result = {"action": "inspect-requests", "items": inspect_requests(binary, config)}
    except (DrivePolicyError, OSError, subprocess.TimeoutExpired) as exc:
        print("PINNED_DRIVE_DENIED: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
