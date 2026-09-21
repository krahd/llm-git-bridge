#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

PROTOCOL = 1
VERSION = "5"
DEFAULT_MAX_TIMEOUT = 300
DEFAULT_POLL_SECONDS = 2.0
DEFAULT_RCLONE_TIMEOUT = 30
DEFAULT_HEALTH_SECONDS = 60.0
DEFAULT_SHELL = "/bin/zsh" if Path("/bin/zsh").exists() else "/bin/bash"
DEFAULT_MAX_REQUEST_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_COMMAND_BYTES = 256 * 1024
DEFAULT_MAX_STDIN_BYTES = 1024 * 1024
DEFAULT_MAX_OUTPUT_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_ACTIVE_CAP = 32
ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")

_TRANSPORT_LOCK = threading.RLock()
_ACTIVE_LOCK = threading.RLock()
_ACTIVE: dict[str, dict] = {}
_SHUTDOWN = threading.Event()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_json_bytes(data: bytes) -> dict:
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("request must be a JSON object")
    return value


def validate_request_name(name: str) -> str:
    if not isinstance(name, str) or not name.endswith(".json"):
        raise ValueError("request filename must be <id>.json")
    rid = name[:-5]
    if rid in {"", ".", ".."} or not ID_RE.fullmatch(rid):
        raise ValueError("invalid request filename/id")
    return rid


def validate_request(req: dict, request_name: str, allowed_root: Path, max_timeout: int,
                     max_command_bytes: int = DEFAULT_MAX_COMMAND_BYTES,
                     max_stdin_bytes: int = DEFAULT_MAX_STDIN_BYTES) -> dict:
    rid_from_name = validate_request_name(request_name)
    if req.get("protocol") != PROTOCOL:
        raise ValueError("unsupported protocol")
    rid = req.get("id")
    if not isinstance(rid, str) or rid != rid_from_name:
        raise ValueError("request filename must equal <id>.json")
    cwd_raw = req.get("cwd")
    if not isinstance(cwd_raw, str) or not cwd_raw:
        raise ValueError("cwd is required")
    cwd = Path(cwd_raw).expanduser().resolve()
    root = allowed_root.expanduser().resolve()
    try:
        cwd.relative_to(root)
    except ValueError as exc:
        raise ValueError("cwd must resolve under allowed_root") from exc
    if not cwd.is_dir():
        raise ValueError("cwd does not exist or is not a directory")
    command = req.get("command")
    if not isinstance(command, str) or not command:
        raise ValueError("command is required")
    if len(command.encode("utf-8")) > max_command_bytes:
        raise ValueError("command is too large")
    timeout = req.get("timeout_seconds", 60)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1 or timeout > max_timeout:
        raise ValueError(f"timeout_seconds must be an integer in 1..{max_timeout}")
    stdin_b64 = req.get("stdin_b64")
    if stdin_b64 is None:
        stdin = b""
    elif isinstance(stdin_b64, str):
        try:
            stdin = base64.b64decode(stdin_b64, validate=True)
        except Exception as exc:
            raise ValueError("stdin_b64 is not valid base64") from exc
    else:
        raise ValueError("stdin_b64 must be a base64 string")
    if len(stdin) > max_stdin_bytes:
        raise ValueError("stdin is too large")
    return {"id": rid, "cwd": cwd, "command": command, "timeout": timeout, "stdin": stdin}


def _terminate_pgid(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            # macOS may report EPERM when probing a process group after its
            # original leader has exited even though TERM was already delivered.
            # Stop polling and make one best-effort KILL attempt instead of
            # turning a successful timeout containment into a bridge failure.
            break
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _terminate_process_group(p: subprocess.Popen) -> None:
    _terminate_pgid(p.pid)


def child_environment(request_id: str | None = None) -> dict:
    env = os.environ.copy()
    if request_id:
        env["CHATGPT_SHELL_BRIDGE_REQUEST_ID"] = request_id
    if sys.platform == "darwin" and Path("/bin/launchctl").is_file():
        try:
            cp = subprocess.run(
                ["/bin/launchctl", "getenv", "SSH_AUTH_SOCK"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=2, check=False,
            )
            value = cp.stdout.decode("utf-8", errors="strict").strip()
            if value:
                env["SSH_AUTH_SOCK"] = value
        except Exception:
            pass
    return env


def run_shell(cwd: Path, command: str, stdin: bytes, timeout: int, shell: str = DEFAULT_SHELL,
              max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
              request_id: str | None = None,
              on_spawn=None) -> dict:
    shell_path = Path(shell)
    if not shell_path.is_absolute() or not shell_path.is_file() or not os.access(shell_path, os.X_OK):
        raise ValueError("configured shell must be an existing absolute file")
    if max_output_bytes < 1:
        raise ValueError("max_output_bytes must be positive")

    started = time.time()
    p = subprocess.Popen(
        [str(shell_path), "-lc", command],
        cwd=str(cwd),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env=child_environment(request_id),
    )
    if on_spawn is not None:
        on_spawn(p.pid, started)
    stdout_buf = bytearray()
    stderr_buf = bytearray()
    limit_event = threading.Event()
    kill_lock = threading.Lock()

    def request_termination() -> None:
        with kill_lock:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def reader(stream, buf):
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    return
                remaining = max_output_bytes - len(buf)
                if remaining > 0:
                    buf.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    limit_event.set()
                    request_termination()
                    return
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def writer():
        if p.stdin is None:
            return
        try:
            if stdin:
                p.stdin.write(stdin)
                p.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                p.stdin.close()
            except Exception:
                pass

    readers = [
        threading.Thread(target=reader, args=(p.stdout, stdout_buf), daemon=True),
        threading.Thread(target=reader, args=(p.stderr, stderr_buf), daemon=True),
    ]
    writer_thread = threading.Thread(target=writer, daemon=True)
    for t in readers:
        t.start()
    writer_thread.start()

    timed_out = False
    aborted = False
    deadline = time.monotonic() + timeout
    try:
        while p.poll() is None:
            if _SHUTDOWN.is_set():
                aborted = True
                _terminate_process_group(p)
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _terminate_process_group(p)
                break
            try:
                p.wait(timeout=min(0.2, remaining))
            except subprocess.TimeoutExpired:
                continue
        if p.poll() is None:
            p.wait()
    finally:
        if p.poll() is None:
            _terminate_process_group(p)
            p.wait()
        for t in readers:
            t.join(timeout=3)
        writer_thread.join(timeout=3)

    finished = time.time()
    stdout = bytes(stdout_buf)
    stderr = bytes(stderr_buf)
    return {
        "exit_code": p.returncode,
        "timed_out": timed_out,
        "aborted_by_daemon_shutdown": aborted,
        "output_limited": limit_event.is_set(),
        "duration_seconds": round(finished - started, 6),
        "stdout_b64": base64.b64encode(stdout).decode("ascii"),
        "stderr_b64": base64.b64encode(stderr).decode("ascii"),
        "stdout_text": stdout.decode("utf-8", errors="replace"),
        "stderr_text": stderr.decode("utf-8", errors="replace"),
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
        "stdout_sha256": sha256_bytes(stdout),
        "stderr_sha256": sha256_bytes(stderr),
    }


def _rclone_prefix(cfg: dict) -> list[str]:
    args: list[str] = []
    root_id = cfg.get("drive_root_folder_id")
    if root_id:
        args.extend(["--drive-root-folder-id", root_id])
    return args


def rclone_target(remote: str, base: str, leaf: str = "", root_pinned: bool = False) -> str:
    remote = remote.rstrip(":") + ":"
    parts = [p.strip("/") for p in ([leaf] if root_pinned else [base, leaf]) if p]
    return remote + "/".join(parts)


def run_rclone(args, cfg: dict | None = None, check=True, capture=True):
    timeout = int((cfg or {}).get("rclone_timeout_seconds", DEFAULT_RCLONE_TIMEOUT))
    argv = ["rclone", *_rclone_prefix(cfg or {}), *args]
    try:
        cp = subprocess.run(
            argv,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"rclone command timed out after {timeout}s") from exc
    if check and cp.returncode != 0:
        err = cp.stderr.decode("utf-8", errors="replace") if cp.stderr else ""
        raise RuntimeError(f"rclone command failed rc={cp.returncode}: {err}")
    return cp


def _target(cfg: dict, leaf: str = "") -> str:
    return rclone_target(
        cfg["remote"], cfg.get("base_path", ""), leaf,
        root_pinned=bool(cfg.get("drive_root_folder_id")),
    )


def ensure_remote_dirs(cfg: dict) -> None:
    for leaf in ["requests", "results"]:
        run_rclone(["mkdir", _target(cfg, leaf)], cfg)


def list_requests_cfg(cfg: dict):
    cp = run_rclone(["lsf", _target(cfg, "requests"), "--files-only"], cfg)
    names = []
    for line in cp.stdout.decode("utf-8", errors="strict").splitlines():
        name = line.strip()
        if not name.endswith(".json") or "/" in name:
            continue
        try:
            validate_request_name(name)
        except ValueError:
            continue
        names.append(name)
    return sorted(names)


def list_requests(remote: str, base: str):
    # Legacy compatibility used by v4 tests.
    return list_requests_cfg({"remote": remote, "base_path": base})


def copy_from_remote_cfg(cfg: dict, leaf: str, local: Path) -> None:
    run_rclone(["copyto", _target(cfg, leaf), str(local)], cfg)


def copy_to_remote_cfg(local: Path, cfg: dict, leaf: str) -> None:
    run_rclone(["copyto", str(local), _target(cfg, leaf)], cfg)


def delete_remote_cfg(cfg: dict, leaf: str) -> None:
    run_rclone(["deletefile", _target(cfg, leaf)], cfg)


def copy_from_remote(remote: str, base: str, leaf: str, local: Path) -> None:
    run_rclone(["copyto", rclone_target(remote, base, leaf), str(local)])


def copy_to_remote(local: Path, remote: str, base: str, leaf: str) -> None:
    run_rclone(["copyto", str(local), rclone_target(remote, base, leaf)])


def delete_remote(remote: str, base: str, leaf: str) -> None:
    run_rclone(["deletefile", rclone_target(remote, base, leaf)])


def result_envelope(rid: str, request_sha: str, status: str, extra: dict) -> dict:
    out = {
        "protocol": PROTOCOL,
        "kind": "result",
        "id": rid,
        "status": status,
        "request_sha256": request_sha,
        "processed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    out.update(extra)
    return out


def _write_upload_delete(result: dict, local_path: Path, name: str, cfg: dict) -> None:
    atomic_write(local_path, json.dumps(result, indent=2, sort_keys=True).encode("utf-8"))
    if cfg.get("drive_root_folder_id"):
        copy_to_remote_cfg(local_path, cfg, f"results/{name}")
        delete_remote_cfg(cfg, f"requests/{name}")
    else:
        copy_to_remote(local_path, cfg["remote"], cfg["base_path"], f"results/{name}")
        delete_remote(cfg["remote"], cfg["base_path"], f"requests/{name}")


def _persist_then_publish(result: dict, local_result: Path, finished_marker: Path,
                          name: str, cfg: dict) -> None:
    atomic_write(local_result, json.dumps(result, indent=2, sort_keys=True).encode("utf-8"))
    atomic_write(finished_marker, json.dumps({
        "status": result["status"],
        "request_sha256": result["request_sha256"],
    }, sort_keys=True).encode("utf-8"))
    if cfg.get("drive_root_folder_id"):
        copy_to_remote_cfg(local_result, cfg, f"results/{name}")
        delete_remote_cfg(cfg, f"requests/{name}")
    else:
        copy_to_remote(local_result, cfg["remote"], cfg["base_path"], f"results/{name}")
        delete_remote(cfg["remote"], cfg["base_path"], f"requests/{name}")


def _stored_sha(marker: Path):
    try:
        value = json.loads(marker.read_text("utf-8"))
    except Exception:
        return None
    sha = value.get("request_sha256") if isinstance(value, dict) else None
    return sha if isinstance(sha, str) else None


def _copy_request(cfg: dict, leaf: str, local: Path) -> None:
    if cfg.get("drive_root_folder_id"):
        copy_from_remote_cfg(cfg, leaf, local)
    else:
        copy_from_remote(cfg["remote"], cfg["base_path"], leaf, local)


def _publish_stored(local_result: Path, name: str, cfg: dict) -> None:
    if cfg.get("drive_root_folder_id"):
        copy_to_remote_cfg(local_result, cfg, f"results/{name}")
        delete_remote_cfg(cfg, f"requests/{name}")
    else:
        copy_to_remote(local_result, cfg["remote"], cfg["base_path"], f"results/{name}")
        delete_remote(cfg["remote"], cfg["base_path"], f"requests/{name}")


def _active_marker_matches(marker: Path, rid: str) -> int | None:
    try:
        v = json.loads(marker.read_text("utf-8"))
        pid = int(v["pgid"])
        if v.get("request_id") != rid or pid <= 1:
            return None
        return pid
    except Exception:
        return None


def _contain_recorded_active(active_marker: Path, rid: str) -> None:
    pgid = _active_marker_matches(active_marker, rid)
    if pgid is None:
        return
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return
    except PermissionError:
        # Existence is ambiguous on macOS; make the same best-effort
        # containment attempt used by the timeout path.
        pass
    _terminate_pgid(pgid)


def process_one(name: str, cfg: dict) -> None:
    rid_guess = validate_request_name(name)
    state_dir = Path(cfg["state_dir"]).expanduser()
    allowed_root = Path(cfg["allowed_root"]).expanduser()
    max_timeout = int(cfg.get("max_timeout_seconds", DEFAULT_MAX_TIMEOUT))
    max_request_bytes = int(cfg.get("max_request_bytes", DEFAULT_MAX_REQUEST_BYTES))
    max_command_bytes = int(cfg.get("max_command_bytes", DEFAULT_MAX_COMMAND_BYTES))
    max_stdin_bytes = int(cfg.get("max_stdin_bytes", DEFAULT_MAX_STDIN_BYTES))
    max_output_bytes = int(cfg.get("max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES))

    request_dir = state_dir / "requests" / rid_guess
    request_dir.mkdir(parents=True, exist_ok=True)
    local_request = request_dir / "request.json"
    local_result = request_dir / "result.json"
    started_marker = request_dir / "started.json"
    finished_marker = request_dir / "finished.json"
    active_marker = request_dir / "active.json"

    fd, tmp = tempfile.mkstemp(prefix="lsb-request-", suffix=".json")
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        _copy_request(cfg, f"requests/{name}", tmp_path)
        if tmp_path.stat().st_size > max_request_bytes:
            request_sha = sha256_file(tmp_path)
            result = result_envelope(rid_guess, request_sha, "rejected", {"message": "request is too large"})
            _write_upload_delete(result, request_dir / "oversize-result.json", name, cfg)
            return
        raw = tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)
    request_sha = sha256_bytes(raw)

    prior_sha = _stored_sha(started_marker) if started_marker.exists() else None
    if prior_sha is None and local_request.exists():
        prior_sha = sha256_bytes(local_request.read_bytes())
    if prior_sha is not None and prior_sha != request_sha:
        result = result_envelope(rid_guess, request_sha, "rejected", {
            "message": "request id was reused with different bytes",
            "prior_request_sha256": prior_sha,
        })
        _write_upload_delete(result, request_dir / "conflict-result.json", name, cfg)
        return

    if finished_marker.exists() and local_result.exists():
        _publish_stored(local_result, name, cfg)
        return

    if started_marker.exists() and not finished_marker.exists():
        _contain_recorded_active(active_marker, rid_guess)
        active_marker.unlink(missing_ok=True)
        result = result_envelope(rid_guess, request_sha, "indeterminate", {
            "message": "daemon previously recorded STARTED without FINISHED; active process was contained if still present; request was not re-executed",
        })
        _persist_then_publish(result, local_result, finished_marker, name, cfg)
        return

    atomic_write(local_request, raw)
    try:
        req = load_json_bytes(raw)
        v = validate_request(req, name, allowed_root, max_timeout, max_command_bytes, max_stdin_bytes)
    except Exception as exc:
        result = result_envelope(rid_guess, request_sha, "rejected", {"message": str(exc)})
        _persist_then_publish(result, local_result, finished_marker, name, cfg)
        return

    atomic_write(started_marker, json.dumps({
        "request_sha256": request_sha,
        "started_at": time.time(),
    }, sort_keys=True).encode("utf-8"))

    def on_spawn(pgid: int, spawned_at: float) -> None:
        atomic_write(active_marker, json.dumps({
            "request_id": v["id"],
            "request_sha256": request_sha,
            "pgid": pgid,
            "spawned_at": spawned_at,
        }, sort_keys=True).encode("utf-8"))
        with _ACTIVE_LOCK:
            _ACTIVE[v["id"]] = {"pgid": pgid, "started_at": spawned_at, "name": name}

    try:
        exec_result = run_shell(
            v["cwd"], v["command"], v["stdin"], v["timeout"],
            cfg.get("shell", DEFAULT_SHELL), max_output_bytes,
            request_id=v["id"], on_spawn=on_spawn,
        )
        if exec_result.get("aborted_by_daemon_shutdown"):
            result = result_envelope(v["id"], request_sha, "indeterminate", {
                "cwd": str(v["cwd"]),
                "message": "daemon shutdown aborted a STARTED request; request was not replayed",
                **exec_result,
            })
        else:
            result = result_envelope(v["id"], request_sha, "completed", {
                "cwd": str(v["cwd"]),
                **exec_result,
            })
    except Exception as exc:
        result = result_envelope(v["id"], request_sha, "indeterminate", {
            "message": f"execution raised after STARTED: {type(exc).__name__}: {exc}",
        })
    finally:
        active_marker.unlink(missing_ok=True)
        with _ACTIVE_LOCK:
            _ACTIVE.pop(v["id"], None)
    _persist_then_publish(result, local_result, finished_marker, name, cfg)


def _auto_active_limit() -> int:
    cpus = os.cpu_count() or 2
    return max(4, min(DEFAULT_MAX_ACTIVE_CAP, cpus * 2))


def max_active_requests(cfg: dict) -> int:
    value = cfg.get("max_active_requests", "auto")
    if value == "auto":
        return _auto_active_limit()
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 256:
        raise ValueError("max_active_requests must be 'auto' or an integer in 1..256")
    return value


def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text("utf-8"))
    for key in ["remote", "allowed_root", "state_dir"]:
        if not isinstance(cfg.get(key), str) or not cfg[key]:
            raise ValueError(f"missing config key: {key}")
    if not cfg.get("drive_root_folder_id"):
        if not isinstance(cfg.get("base_path"), str) or not cfg["base_path"]:
            raise ValueError("missing config key: base_path (or drive_root_folder_id)")
    if not Path(cfg["allowed_root"]).expanduser().resolve().is_dir():
        raise ValueError("allowed_root does not exist or is not a directory")
    shell = cfg.get("shell", DEFAULT_SHELL)
    if not isinstance(shell, str) or not Path(shell).is_absolute() or not Path(shell).is_file() or not os.access(shell, os.X_OK):
        raise ValueError("configured shell must be an existing absolute file")
    poll = cfg.get("poll_seconds", DEFAULT_POLL_SECONDS)
    if isinstance(poll, bool) or not isinstance(poll, (int, float)) or poll <= 0:
        raise ValueError("poll_seconds must be positive")
    for key, default in [
        ("max_timeout_seconds", DEFAULT_MAX_TIMEOUT),
        ("rclone_timeout_seconds", DEFAULT_RCLONE_TIMEOUT),
        ("max_request_bytes", DEFAULT_MAX_REQUEST_BYTES),
        ("max_command_bytes", DEFAULT_MAX_COMMAND_BYTES),
        ("max_stdin_bytes", DEFAULT_MAX_STDIN_BYTES),
        ("max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES),
    ]:
        value = cfg.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{key} must be a positive integer")
    max_active_requests(cfg)
    return cfg


def acquire_instance_lock(cfg: dict):
    state_dir = Path(cfg["state_dir"]).expanduser()
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_file = (state_dir / "daemon.lock").open("a+")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_file.close()
        raise RuntimeError("another shell-bridge instance already holds the state lock") from exc
    return lock_file


def _result_state_counts(cfg: dict) -> dict:
    root = Path(cfg["state_dir"]).expanduser() / "requests"
    out = {"started_without_finished": 0, "finished": 0}
    if not root.exists():
        return out
    for d in root.iterdir():
        if not d.is_dir():
            continue
        started = (d / "started.json").exists()
        finished = (d / "finished.json").exists()
        if started and not finished:
            out["started_without_finished"] += 1
        if finished:
            out["finished"] += 1
    return out


def doctor(cfg: dict, *, probe_transport: bool = True) -> dict:
    info = {
        "protocol": PROTOCOL,
        "bridge_version": VERSION,
        "bridge_instance_id": cfg.get("bridge_instance_id"),
        "remote": cfg["remote"],
        "base_path": cfg.get("base_path"),
        "drive_root_folder_id": cfg.get("drive_root_folder_id"),
        "requests_folder_id": cfg.get("requests_folder_id"),
        "results_folder_id": cfg.get("results_folder_id"),
        "allowed_root": str(Path(cfg["allowed_root"]).expanduser().resolve()),
        "max_active_requests": max_active_requests(cfg),
        "rclone_timeout_seconds": int(cfg.get("rclone_timeout_seconds", DEFAULT_RCLONE_TIMEOUT)),
        "state": _result_state_counts(cfg),
    }
    with _ACTIVE_LOCK:
        info["active_requests"] = sorted(_ACTIVE)
    if probe_transport:
        try:
            cp = run_rclone(["version"], cfg, check=False)
            text = (cp.stdout or b"").decode("utf-8", errors="replace")
            info["rclone_version"] = text.splitlines()[0] if text else None
        except Exception as exc:
            info["rclone_version_error"] = str(exc)
        try:
            cp = run_rclone(["about", _target(cfg)], cfg, check=False)
            err = (cp.stderr or b"").decode("utf-8", errors="replace")
            info["drive_connectivity_ok"] = cp.returncode == 0
            info["shared_oauth_client_warning"] = "shared Google Drive client_id" in err
            if cp.returncode != 0:
                info["drive_error"] = err[-2000:]
        except Exception as exc:
            info["drive_connectivity_ok"] = False
            info["drive_error"] = str(exc)
    return info


def publish_health(cfg: dict) -> None:
    state_dir = Path(cfg["state_dir"]).expanduser()
    payload = doctor(cfg, probe_transport=False)
    payload["kind"] = "shell_bridge_health"
    payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    local = state_dir / "health.json"
    atomic_write(local, json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"))
    try:
        health_cfg = dict(cfg)
        health_cfg["rclone_timeout_seconds"] = min(5, int(cfg.get("rclone_timeout_seconds", DEFAULT_RCLONE_TIMEOUT)))
        if cfg.get("drive_root_folder_id"):
            copy_to_remote_cfg(local, health_cfg, "health.json")
        else:
            # Legacy helper has no cfg timeout override; v5 installations pin root ID.
            copy_to_remote(local, cfg["remote"], cfg["base_path"], "health.json")
    except Exception as exc:
        print(f"health publish error: {type(exc).__name__}: {exc}", flush=True)


def sweep_incomplete_processes(cfg: dict) -> int:
    """Contain any recorded process groups before polling after daemon restart.

    Do not manufacture FINISHED without the remote request bytes; process_one will
    publish the hash-bound indeterminate result when that request is rediscovered.
    """
    root = Path(cfg["state_dir"]).expanduser() / "requests"
    swept = 0
    if not root.is_dir():
        return swept
    for d in root.iterdir():
        if not d.is_dir() or not (d / "started.json").exists() or (d / "finished.json").exists():
            continue
        active = d / "active.json"
        if active.exists():
            _contain_recorded_active(active, d.name)
            active.unlink(missing_ok=True)
            swept += 1
    return swept


def _worker(name: str, cfg: dict, active_names: set[str], active_lock: threading.Lock) -> None:
    try:
        process_one(name, cfg)
    except Exception as exc:
        print(f"request {name}: {type(exc).__name__}: {exc}", flush=True)
    finally:
        with active_lock:
            active_names.discard(name)


def _signal_shutdown(_signum, _frame):
    _SHUTDOWN.set()
    with _ACTIVE_LOCK:
        pgids = [int(v["pgid"]) for v in _ACTIVE.values() if v.get("pgid")]
    for pgid in pgids:
        _terminate_pgid(pgid)


def daemon(config_path: Path) -> None:
    cfg = load_config(config_path)
    lock_file = acquire_instance_lock(cfg)
    ensure_remote_dirs(cfg)
    swept = sweep_incomplete_processes(cfg)
    if swept:
        print(f"startup contained {swept} recorded incomplete process group(s)", flush=True)
    poll = float(cfg.get("poll_seconds", DEFAULT_POLL_SECONDS))
    limit = max_active_requests(cfg)
    health_seconds = float(cfg.get("health_seconds", DEFAULT_HEALTH_SECONDS))
    active_names: set[str] = set()
    active_lock = threading.Lock()
    threads: set[threading.Thread] = set()
    last_health = 0.0
    _SHUTDOWN.clear()
    old_handlers = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        old_handlers[sig] = signal.signal(sig, _signal_shutdown)
    try:
        while not _SHUTDOWN.is_set():
            threads = {t for t in threads if t.is_alive()}
            try:
                names = list_requests_cfg(cfg)
                for name in names:
                    if _SHUTDOWN.is_set():
                        break
                    with active_lock:
                        if name in active_names:
                            continue
                        if len(active_names) >= limit:
                            break
                        active_names.add(name)
                    t = threading.Thread(target=_worker, args=(name, cfg, active_names, active_lock), daemon=True)
                    threads.add(t)
                    t.start()
            except Exception as exc:
                print(f"poll error: {type(exc).__name__}: {exc}", flush=True)
            now = time.monotonic()
            if now - last_health >= health_seconds:
                publish_health(cfg)
                last_health = now
            _SHUTDOWN.wait(poll)
    finally:
        _SHUTDOWN.set()
        _signal_shutdown(signal.SIGTERM, None)
        deadline = time.monotonic() + 5
        for t in list(threads):
            t.join(timeout=max(0.0, deadline - time.monotonic()))
        lock_file.close()
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


def once(config_path: Path) -> None:
    cfg = load_config(config_path)
    lock_file = acquire_instance_lock(cfg)
    try:
        ensure_remote_dirs(cfg)
        for name in list_requests_cfg(cfg):
            process_one(name, cfg)
    finally:
        lock_file.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["daemon", "once", "doctor"])
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    path = Path(args.config).expanduser()
    if args.mode == "daemon":
        daemon(path)
    elif args.mode == "once":
        once(path)
    else:
        print(json.dumps(doctor(load_config(path)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
