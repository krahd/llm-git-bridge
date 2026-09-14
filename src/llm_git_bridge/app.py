from __future__ import annotations

import argparse
import configparser
import hashlib
import hmac
import json
import math
import os
import plistlib
import queue
import re
import secrets
import shutil
import signal
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Collection

from .core import (
    BridgeError,
    PROTOCOL_VERSION,
    add_disposable_worktree,
    branch_tip,
    branch_token,
    build_registry,
    build_snapshot,
    git,
    load_json,
    process_transaction,
    public_registry,
    repo_state,
    retire_worktree,
    run,
    resolve_repo,
    save_json,
    strict_json_loads,
    utc_now,
    validate_safe_branch_name,
    validate_transaction_id,
)
from .transport import (
    RcloneRCProcess,
    RcloneTransport,
    RemoteFileEntry,
    TransientTransportError,
    start_rclone_rcd,
)
from . import __version__

APP_NAME = "llm-git-bridge"
CONFIG_DIR = Path.home() / ".config" / APP_NAME
STATE_DIR = Path.home() / ".local" / "state" / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
REGISTRY_FILE = STATE_DIR / "registry.json"
PUBLISHED_DIR = STATE_DIR / "published-results"
LABEL = "io.llm-git-bridge.daemon"
REMOTE_ROOT = "v2"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
RCLONE_RC_SOCKET = STATE_DIR / "rclone-rc.sock"
RCD_HEALTH_TIMEOUT_S = 0.75
RCD_HEALTH_FAILURE_THRESHOLD = 2
TX_FILENAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}\.json")
MAX_REQUEST_BYTES = 12_000_000
MAX_METRICS_BYTES = 2_000_000
LOCAL_RESULT_RETENTION = 200
COMMAND_LOG_RETENTION = 10
RESULT_RETRY_GRACE_S = 15.0
MAX_RESULT_BYTES = 2_000_000
RESULT_AUTH_ALG = "hmac-sha256"


def _ensure_private_dirs() -> None:
    for path in (CONFIG_DIR, STATE_DIR):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass


def default_config() -> dict[str, Any]:
    return {
        "version": 1,
        "transport": {"type": "rclone", "remote": None, "rc_enabled": True},
        "roots": [],
        "safe_branch_prefix": "ai/",
        "allow_commit": True,
        "push_enabled_repos": [],
        "poll_interval": 1.0,
        "max_workers": 1,
        "max_pending_jobs": 8,
        "commands": {},
    }


def _validate_poll_interval(value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0.5 <= float(value) <= 3600.0
    ):
        raise BridgeError("poll interval must be a finite number from 0.5 to 3600 seconds")
    return float(value)


def _validate_config(cfg: dict[str, Any]) -> dict[str, Any]:
    if cfg.get("version") != 1:
        raise BridgeError("unsupported config version")

    transport = cfg.get("transport")
    if not isinstance(transport, dict):
        raise BridgeError("config transport must be an object")
    if transport.get("type") != "rclone":
        raise BridgeError("config transport type must be 'rclone'")
    remote = transport.get("remote")
    if remote is not None and (not isinstance(remote, str) or not remote.strip()):
        raise BridgeError("config transport remote must be a non-empty string or null")
    if not isinstance(transport.get("rc_enabled"), bool):
        raise BridgeError("config transport rc_enabled must be a boolean")

    roots = cfg.get("roots")
    if not isinstance(roots, list) or not all(isinstance(x, str) and x.strip() for x in roots):
        raise BridgeError("config roots must be a list of non-empty paths")

    prefix = cfg.get("safe_branch_prefix")
    if (
        not isinstance(prefix, str)
        or not prefix.endswith("/")
        or len(prefix) > 100
        or not re.fullmatch(r"[A-Za-z0-9._/-]+/", prefix)
    ):
        raise BridgeError("safe_branch_prefix must be a safe branch namespace ending in '/'")
    prefix_body = prefix[:-1]
    components = prefix_body.split("/")
    if (
        prefix.startswith("-")
        or ".." in prefix
        or "//" in prefix
        or "/." in prefix
        or any(not part or part.startswith(".") or part.endswith((".", ".lock")) for part in components)
    ):
        raise BridgeError("safe_branch_prefix contains an unsafe Git ref component")

    if not isinstance(cfg.get("allow_commit"), bool):
        raise BridgeError("config allow_commit must be a boolean")
    push_enabled = cfg.get("push_enabled_repos")
    if not isinstance(push_enabled, list) or not all(isinstance(x, str) and x for x in push_enabled):
        raise BridgeError("config push_enabled_repos must be a list of repository IDs")

    try:
        _validate_poll_interval(cfg.get("poll_interval"))
    except BridgeError as exc:
        raise BridgeError("config poll_interval must be a finite number from 0.5 to 3600 seconds") from exc

    max_workers = cfg.get("max_workers")
    if (
        isinstance(max_workers, bool)
        or not isinstance(max_workers, int)
        or not 1 <= max_workers <= 8
    ):
        raise BridgeError("config max_workers must be an integer from 1 to 8")

    max_pending_jobs = cfg.get("max_pending_jobs")
    if (
        isinstance(max_pending_jobs, bool)
        or not isinstance(max_pending_jobs, int)
        or not 1 <= max_pending_jobs <= 32
    ):
        raise BridgeError("config max_pending_jobs must be an integer from 1 to 32")
    if max_pending_jobs < max_workers:
        raise BridgeError("config max_pending_jobs must be at least max_workers")

    commands = cfg.get("commands")
    if not isinstance(commands, dict):
        raise BridgeError("config commands must be an object keyed by repository ID")
    for repo_id, repo_commands in commands.items():
        if not isinstance(repo_id, str) or not repo_id:
            raise BridgeError("config command repository IDs must be non-empty strings")
        if not isinstance(repo_commands, dict):
            raise BridgeError("config commands must be nested by repository ID")
        for name, argv in repo_commands.items():
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
                raise BridgeError("config command name is invalid")
            if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
                raise BridgeError(f"configured command {name!r} must be a non-empty argv list")
    return cfg


def load_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return default_config()
    raw = load_json(CONFIG_FILE)
    base = default_config()
    transport = raw.pop("transport", None) if "transport" in raw else None
    base.update(raw)
    if transport is not None:
        if not isinstance(transport, dict):
            raise BridgeError("config transport must be an object")
        base["transport"].update(transport)
    return _validate_config(base)


def save_config(cfg: dict[str, Any]) -> None:
    save_json(CONFIG_FILE, _validate_config(cfg))


def detect_rclone_remote() -> str:
    if shutil.which("rclone") is None:
        raise BridgeError("rclone is not installed")
    proc = run(["rclone", "listremotes"], timeout=20)
    remotes = {line.strip().rstrip(":") for line in proc.stdout.splitlines() if line.strip()}
    if "llm-git-bridge" in remotes:
        return "llm-git-bridge"
    raise BridgeError("no llm-git-bridge: rclone remote found; pass --remote <name> to use another configured remote")


def transport_from_config(cfg: dict[str, Any]) -> RcloneTransport:
    transport = cfg.get("transport", {})
    if transport.get("type") != "rclone":
        raise BridgeError(f"unsupported transport: {transport.get('type')!r}")
    remote = transport.get("remote")
    if not remote:
        raise BridgeError("transport remote is not configured; run setup")
    rc_socket = RCLONE_RC_SOCKET if bool(transport.get("rc_enabled", True)) else None
    return RcloneTransport(str(remote), rc_socket=rc_socket)


def publish_registry(cfg: dict[str, Any], registry: dict[str, Any]) -> None:
    transport = transport_from_config(cfg)
    transport.upload_json(
        f"{REMOTE_ROOT}/meta/repos.json",
        public_registry(registry),
        STATE_DIR / "outbox" / "repos.json",
    )


def refresh_registry(cfg: dict[str, Any], *, publish: bool = True) -> dict[str, Any]:
    roots = [Path(x) for x in cfg.get("roots", [])]
    previous = load_json(REGISTRY_FILE) if REGISTRY_FILE.exists() else None
    registry = build_registry(roots, previous=previous)
    save_json(REGISTRY_FILE, registry)
    if publish:
        publish_registry(cfg, registry)
    return registry


def load_registry() -> dict[str, Any]:
    if not REGISTRY_FILE.exists():
        raise BridgeError("registry does not exist; run add-root or scan")
    return load_json(REGISTRY_FILE)


def refresh_repo_entry(cfg: dict[str, Any], repo_ref: str, *, publish: bool = True) -> tuple[dict[str, Any], str, dict[str, Any]]:
    registry = load_registry()
    repo_id, entry = resolve_repo(registry, repo_ref)
    path = Path(entry["path"])
    state = repo_state(path)
    entry.update(
        {
            "head": state["head"],
            "branch": state["branch"],
            "dirty": state["dirty"],
            "tracked_dirty": state["tracked_dirty"],
            "untracked": state["untracked"],
            "last_seen": utc_now(),
        }
    )
    registry["generated_at"] = utc_now()
    save_json(REGISTRY_FILE, registry)
    if publish:
        publish_registry(cfg, registry)
    return registry, repo_id, entry


def _materialize_with_snapshot(
    cfg: dict[str, Any],
    repo_ref: str,
    *,
    branch_snapshot: tuple[Path, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    registry = load_registry()
    repo_id, entry = resolve_repo(registry, repo_ref)
    repo_path = branch_snapshot[0] if branch_snapshot else Path(entry["path"])
    snapshot = build_snapshot(repo_path, repo_id)
    if branch_snapshot:
        branch = branch_snapshot[1]
        snapshot["branch"] = branch
        remote_rel = f"{REMOTE_ROOT}/repos/{repo_id}/branches/{branch_token(branch)}/snapshot.json"
    else:
        remote_rel = f"{REMOTE_ROOT}/repos/{repo_id}/snapshot.json"
    transport = transport_from_config(cfg)
    transport.upload_json(remote_rel, snapshot, STATE_DIR / "outbox" / f"snapshot-{repo_id}.json")
    return remote_rel, snapshot


def materialize(cfg: dict[str, Any], repo_ref: str, *, branch_snapshot: tuple[Path, str] | None = None) -> str:
    return _materialize_with_snapshot(cfg, repo_ref, branch_snapshot=branch_snapshot)[0]


def _published_marker(filename: str) -> Path:
    return PUBLISHED_DIR / filename


def _quarantine_corrupt_marker(filename: str, marker_path: Path) -> None:
    quarantine_dir = STATE_DIR / "corrupt-markers"
    quarantine_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(quarantine_dir, 0o700)
    except OSError:
        pass
    try:
        os.replace(marker_path, quarantine_dir / filename)
    except OSError:
        marker_path.unlink(missing_ok=True)
    _append_metric({
        "event": "publication-marker-quarantined",
        "transaction_id": filename[:-5],
        "recorded_at": utc_now(),
    })


def _load_published_marker(filename: str) -> dict[str, Any] | None:
    marker_path = _published_marker(filename)
    if not marker_path.exists():
        return None
    try:
        marker = load_json(marker_path)
    except BridgeError:
        _quarantine_corrupt_marker(filename, marker_path)
        return None
    request_bytes_sha256 = marker.get("request_bytes_sha256")
    if (
        marker.get("filename") != filename
        or not isinstance(marker.get("published_at"), str)
        or not isinstance(marker.get("source"), str)
        or (
            request_bytes_sha256 is not None
            and (not isinstance(request_bytes_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", request_bytes_sha256))
        )
    ):
        _quarantine_corrupt_marker(filename, marker_path)
        return None
    return marker


def _mark_published(filename: str, *, source: str, request_bytes_sha256: str | None = None) -> None:
    marker: dict[str, Any] = {"filename": filename, "published_at": utc_now(), "source": source}
    if request_bytes_sha256 is not None:
        marker["request_bytes_sha256"] = request_bytes_sha256
    save_json(_published_marker(filename), marker)


def _validate_request_identity(obj: dict[str, Any], filename: str) -> str:
    if obj.get("protocol") != PROTOCOL_VERSION:
        raise BridgeError(f"unsupported protocol: {obj.get('protocol')!r}")
    txid = validate_transaction_id(obj.get("transaction_id"))
    if f"{txid}.json" != filename:
        raise BridgeError("transaction_id does not match filename")
    return txid


def _read_result_auth_key(path: Path) -> bytes:
    try:
        raw = path.read_text(encoding="ascii").strip()
        key = bytes.fromhex(raw)
    except (OSError, UnicodeError, ValueError) as exc:
        raise BridgeError("result authentication key is unreadable") from exc
    if len(key) != 32:
        raise BridgeError("result authentication key has invalid length")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def _publish_result_auth_key(path: Path, key: bytes) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, tmp_name = tempfile.mkstemp(prefix="result-auth.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="\n") as fh:
            fh.write(key.hex() + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        try:
            # Hard-link publication is atomic and refuses to replace an existing
            # key if another bridge process won the race.
            os.link(tmp, path)
        except FileExistsError:
            return _read_result_auth_key(path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return key
    finally:
        tmp.unlink(missing_ok=True)


def _result_auth_key() -> bytes:
    """Return the durable private key used to authenticate acknowledgements.

    Replay authentication must survive deletion of disposable runtime state. New
    keys therefore live with persistent bridge configuration. The previous
    state-directory location is migrated once so an upgrade does not invalidate
    already-authenticated remote results.
    """
    _ensure_private_dirs()
    path = CONFIG_DIR / "result-auth.key"
    if path.exists():
        return _read_result_auth_key(path)

    legacy = STATE_DIR / "result-auth.key"
    if legacy.exists():
        return _publish_result_auth_key(path, _read_result_auth_key(legacy))

    return _publish_result_auth_key(path, secrets.token_bytes(32))


def _result_auth_tag(filename: str, result: dict[str, Any]) -> str:
    unsigned = dict(result)
    unsigned.pop("bridge_auth", None)
    payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    message = filename.encode("utf-8") + b"\x00" + payload
    return hmac.new(_result_auth_key(), message, hashlib.sha256).hexdigest()


def _sign_result(filename: str, result: dict[str, Any]) -> dict[str, Any]:
    signed = dict(result)
    signed["bridge_auth"] = {"alg": RESULT_AUTH_ALG, "tag": _result_auth_tag(filename, signed)}
    return signed


def _result_identity_is_valid(filename: str, result: dict[str, Any]) -> bool:
    txid = result.get("transaction_id")
    if not isinstance(txid, str) or not TX_FILENAME_RE.fullmatch(filename):
        return False
    if f"{txid}.json" != filename:
        return False
    if result.get("status") not in {"success", "error"}:
        return False
    protocol = result.get("protocol")
    if protocol is not None and protocol != PROTOCOL_VERSION:
        return False
    kind = result.get("kind")
    if kind is not None and kind != "result":
        return False
    request_bytes_sha256 = result.get("request_bytes_sha256")
    if request_bytes_sha256 is not None and (
        not isinstance(request_bytes_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", request_bytes_sha256)
    ):
        return False
    return True


def _verify_result(filename: str, result: dict[str, Any]) -> bool:
    if not _result_identity_is_valid(filename, result):
        return False
    auth = result.get("bridge_auth")
    if not isinstance(auth, dict) or auth.get("alg") != RESULT_AUTH_ALG:
        return False
    tag = auth.get("tag")
    if not isinstance(tag, str) or not re.fullmatch(r"[0-9a-f]{64}", tag):
        return False
    expected = _result_auth_tag(filename, result)
    return hmac.compare_digest(tag, expected)


def _load_authentic_remote_result(transport: RcloneTransport, filename: str) -> dict[str, Any] | None:
    tmp = STATE_DIR / "reconcile" / filename
    try:
        raw = transport.download_text(
            f"{REMOTE_ROOT}/results/{filename}",
            tmp,
            max_bytes=MAX_RESULT_BYTES,
        )
        obj = strict_json_loads(raw.lstrip("\ufeff"))
        if isinstance(obj, dict) and _verify_result(filename, obj):
            return obj
    except Exception:
        return None
    finally:
        tmp.unlink(missing_ok=True)
    return None


def _remote_result_is_authentic(transport: RcloneTransport, filename: str) -> bool:
    return _load_authentic_remote_result(transport, filename) is not None


def _public_error_message(exc: Exception) -> str:
    if not isinstance(exc, BridgeError):
        return "internal bridge error"
    message = str(exc).replace(str(Path.home()), "<home>")
    message = re.sub(r"(?i)(https?://)[^/@\s]+:[^/@\s]+@", r"\1<redacted>@", message)
    message = message.replace("\n", " ").replace("\r", " ")
    return message[:500] or "bridge error"


_METRIC_PUBLIC_KEYS = {
    "event",
    "transaction_id",
    "transaction_list_s",
    "transaction_download_s",
    "pre_result_upload_s",
    "result_upload_s",
    "request_total_s",
    "poll_total_s",
    "results_list_s",
    "remote_results",
    "markers_added",
    "recorded_at",
    "transaction_list_transport",
    "transaction_download_transport",
    "result_upload_transport",
    "request_cleanup_s",
    "request_cleanup_status",
    "request_cleanup_transport",
    "queue_depth",
    "active_workers",
    "active_repos",
    "queue_wait_s",
    "execution_s",
    "scheduler_utilization",
}


def _recent_metrics(limit: int) -> list[dict[str, Any]]:
    path = STATE_DIR / "metrics.jsonl"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    metrics: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        metrics.append({key: item[key] for key in _METRIC_PUBLIC_KEYS if key in item})
    return metrics


def _process_diagnostics_request(
    obj: dict[str, Any],
    filename: str,
    *,
    scheduler_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    txid = _validate_request_identity(obj, filename)
    limit = obj.get("limit", 10)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise BridgeError("diagnostics limit must be an integer from 1 to 50")
    result = {
        "protocol": PROTOCOL_VERSION,
        "kind": "result",
        "operation": "diagnostics",
        "transaction_id": txid,
        "status": "success",
        "metrics": _recent_metrics(limit),
        "processed_at": utc_now(),
    }
    if scheduler_status is not None:
        result["scheduler"] = dict(scheduler_status)
    return result


def _version_line(argv: list[str]) -> str | None:
    proc = run(argv, check=False, timeout=5)
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line:
            return line[:160]
    return None


def _rclone_remote_summary(remote: str) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "remote_type": None,
        "custom_drive_client_id_configured": None,
        "config_inspected": False,
    }
    proc = run(["rclone", "config", "file"], check=False, timeout=5)
    if proc.returncode != 0:
        return summary
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        return summary
    config_path = Path(lines[-1]).expanduser()
    parser = configparser.RawConfigParser(interpolation=None)
    try:
        with config_path.open("r", encoding="utf-8") as fh:
            parser.read_file(fh)
    except (OSError, configparser.Error, UnicodeError):
        return summary
    if not parser.has_section(remote):
        return summary
    summary["config_inspected"] = True
    remote_type = parser.get(remote, "type", fallback="").strip()
    summary["remote_type"] = remote_type[:40] or None
    if remote_type == "drive":
        summary["custom_drive_client_id_configured"] = bool(parser.get(remote, "client_id", fallback="").strip())
    return summary


def _process_doctor_request(cfg: dict[str, Any], obj: dict[str, Any], filename: str) -> dict[str, Any]:
    txid = _validate_request_identity(obj, filename)
    transport_cfg = cfg.get("transport", {})
    remote = str(transport_cfg.get("remote") or "")
    rc_enabled = bool(transport_cfg.get("rc_enabled", True))
    remote_summary = _rclone_remote_summary(remote) if remote else {
        "remote_type": None,
        "custom_drive_client_id_configured": None,
        "config_inspected": False,
    }
    rc_healthy = False
    if rc_enabled:
        rc_healthy = RcloneRCProcess(RCLONE_RC_SOCKET, None).healthy(timeout=0.25)
    return {
        "protocol": PROTOCOL_VERSION,
        "kind": "result",
        "operation": "doctor",
        "transaction_id": txid,
        "status": "success",
        "doctor": {
            "bridge_version": __version__,
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "git_version": _version_line(["git", "--version"]),
            "rclone_version": _version_line(["rclone", "version"]),
            "transport_type": transport_cfg.get("type"),
            "rc_enabled": rc_enabled,
            "rc_socket_healthy": rc_healthy,
            **remote_summary,
        },
        "processed_at": utc_now(),
    }


def _process_materialize_request(cfg: dict[str, Any], registry: dict[str, Any], obj: dict[str, Any], filename: str) -> dict[str, Any]:
    txid = _validate_request_identity(obj, filename)
    repo_ref = obj.get("repo")
    if not isinstance(repo_ref, str) or not repo_ref.strip():
        raise BridgeError("materialize request is missing repo")
    repo_id, entry = resolve_repo(registry, repo_ref)
    branch = obj.get("branch")

    if branch is None:
        registry, repo_id, entry = refresh_repo_entry(cfg, repo_id, publish=True)
        snapshot_rel, snapshot = _materialize_with_snapshot(cfg, repo_id)
        head = snapshot["head"]
        branch_name = snapshot["branch"]
    else:
        branch_name = validate_safe_branch_name(branch, str(cfg.get("safe_branch_prefix", "ai/")))
        repo_path = Path(entry["path"])
        head = branch_tip(repo_path, branch_name)
        if head is None:
            raise BridgeError(f"unknown local branch: {branch_name}")

        wt = STATE_DIR / "materialize-worktrees" / txid
        retire_worktree(repo_path, wt)
        try:
            add_disposable_worktree(repo_path, "--detach", str(wt), head)
            snapshot_rel, snapshot = _materialize_with_snapshot(
                cfg, repo_id, branch_snapshot=(wt, branch_name)
            )
            if snapshot.get("head") != head:
                raise BridgeError("materialized branch snapshot HEAD changed unexpectedly")
        finally:
            retire_worktree(repo_path, wt)

    return {
        "protocol": PROTOCOL_VERSION,
        "kind": "result",
        "operation": "materialize",
        "transaction_id": txid,
        "repo": repo_id,
        "status": "success",
        "head": head,
        "branch": branch_name,
        "snapshot": snapshot_rel,
        "processed_at": utc_now(),
    }


def _append_metric(event: dict[str, Any]) -> None:
    # Observability must never alter transaction or replay semantics. Keep a
    # bounded current+previous log so a long-running daemon does not grow state
    # without limit.
    try:
        path = STATE_DIR / "metrics.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size >= MAX_METRICS_BYTES:
            previous = path.with_name("metrics.jsonl.1")
            previous.unlink(missing_ok=True)
            os.replace(path, previous)
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
    except OSError:
        pass


def _cleanup_remote_request(transport: RcloneTransport, filename: str, *, allow_fallback: bool = True) -> tuple[str, float, str]:
    started = time.monotonic()
    status = "success"
    try:
        deleted = transport.delete_file(
            f"{REMOTE_ROOT}/transactions/{filename}",
            allow_fallback=allow_fallback,
        )
        if not deleted:
            status = "deferred"
    except Exception:
        status = "error"
    return status, round(time.monotonic() - started, 4), str(getattr(transport, "last_mode", "unknown"))


def _cleanup_local_request_artifacts(filename: str) -> None:
    txid = filename[:-5]
    (STATE_DIR / "inbox" / filename).unlink(missing_ok=True)
    for parent in ("transactions", "command-runs", "command-homes"):
        shutil.rmtree(STATE_DIR / parent / txid, ignore_errors=True)

    # Outbox JSON is only a staging source for rclone. Replay durability lives in
    # results/<tx>.json plus the publication marker, so successful acknowledgement
    # can remove per-transaction staging files instead of leaking one forever.
    outbox = STATE_DIR / "outbox"
    (outbox / f"result-{filename}").unlink(missing_ok=True)
    if outbox.exists():
        for path in outbox.glob(f"snapshot-*-{txid}.json"):
            path.unlink(missing_ok=True)


def reconcile_local_acknowledged_artifacts() -> int:
    """Retire crash-left transient state whose acknowledgement is durable.

    A process can die after the remote request has already been deleted but
    before `_cleanup_local_request_artifacts` finishes. With no inbox object
    left, ordinary polling cannot rediscover that transaction. Derive cleanup
    candidates from local transient state and trust only an existing valid local
    publication marker. Outbox JSON is staging, never replay authority, and may
    be removed unconditionally at watcher startup because durable local results
    live under `results/`.
    """
    txids: set[str] = set()
    inbox = STATE_DIR / "inbox"
    if inbox.exists():
        for path in inbox.glob("*.json"):
            if TX_FILENAME_RE.fullmatch(path.name):
                txids.add(path.stem)
    for parent_name in ("transactions", "command-runs", "command-homes"):
        parent = STATE_DIR / parent_name
        if not parent.exists():
            continue
        try:
            children = list(parent.iterdir())
        except OSError:
            continue
        for path in children:
            if path.is_dir() and TX_FILENAME_RE.fullmatch(path.name + ".json"):
                txids.add(path.name)

    cleaned = 0
    for txid in sorted(txids):
        filename = f"{txid}.json"
        if _load_published_marker(filename) is None:
            continue
        _cleanup_local_request_artifacts(filename)
        cleaned += 1

    outbox = STATE_DIR / "outbox"
    if outbox.exists():
        try:
            staging = list(outbox.iterdir())
        except OSError:
            staging = []
        for path in staging:
            if path.is_file() and (path.name.startswith("result-") or path.name.startswith("snapshot-")):
                path.unlink(missing_ok=True)

    if cleaned:
        _append_metric({
            "event": "startup-local-cleanup",
            "cleaned_transactions": cleaned,
            "recorded_at": utc_now(),
        })
    return cleaned


def _prune_command_logs(
    limit: int = COMMAND_LOG_RETENTION,
    *,
    protected_txids: Collection[str] = (),
) -> None:
    """Retain bounded diagnostic logs without deleting active worker logs."""
    if limit < 1:
        return
    root = STATE_DIR / "command-logs"
    if not root.exists():
        return
    try:
        protected = frozenset(str(txid) for txid in protected_txids)
        candidates = [
            path for path in root.iterdir()
            if path.is_dir() and path.name not in protected
        ]
        candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        for path in candidates[limit:]:
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def _prune_local_results(limit: int = LOCAL_RESULT_RETENTION) -> None:
    """Bound durable local result copies after remote acknowledgement.

    Replay safety lives in the much smaller publication marker plus the remote
    result. Local result JSON is retained only as a recent diagnostic/retry cache.

    The steady state normally contains exactly ``limit`` acknowledged results.
    Validate publication markers only for the oldest files that actually need
    deletion instead of reparsing every retained marker on every transaction.
    Unacknowledged results remain durable even when they are older than the cache.
    """
    if limit < 1:
        return
    result_dir = STATE_DIR / "results"
    if not result_dir.exists():
        return
    try:
        candidates = [path for path in result_dir.glob("*.json") if path.is_file()]
        excess = len(candidates) - limit
        if excess <= 0:
            return
        candidates.sort(key=lambda path: path.stat().st_mtime)
        deleted = 0
        for path in candidates:
            if _load_published_marker(path.name) is None:
                continue
            path.unlink(missing_ok=True)
            deleted += 1
            if deleted >= excess:
                break
    except OSError:
        pass


class _CorruptLocalResult(BridgeError):
    """A durable local acknowledgement exists but cannot be trusted/read."""


@dataclass(frozen=True)
class _TransactionPoll:
    """One immutable view of the remote transaction inbox."""

    entries: tuple[RemoteFileEntry, ...]
    list_s: float
    list_transport: str

    @property
    def filenames(self) -> tuple[str, ...]:
        return tuple(entry.name for entry in self.entries)

    def size_for(self, filename: str) -> int | None:
        for entry in self.entries:
            if entry.name == filename:
                return entry.size
        return None


@dataclass(frozen=True)
class _MailboxClassification:
    """Requests eligible for execution versus already acknowledged requests."""

    candidates: tuple[str, ...]
    published: tuple[str, ...]


@dataclass(frozen=True)
class _DownloadedRequest:
    """Exact downloaded request bytes plus transport metadata."""

    filename: str
    raw: str
    request_bytes_sha256: str
    download_s: float
    download_transport: str


@dataclass(frozen=True)
class _ValidatedRequest:
    """A protocol-validated request ready for local dispatch."""

    downloaded: _DownloadedRequest
    obj: dict[str, Any]


@dataclass(frozen=True)
class _LocalJob:
    """Scheduler-facing classification of one validated request."""

    request: _ValidatedRequest
    kind: str
    resource_class: str
    scheduling_key: str | None


@dataclass(frozen=True)
class _TransactionWorkerTask:
    """Immutable local-execution handoff for the B2 worker boundary."""

    filename: str
    request_raw: str
    repo_id: str
    repo_path: Path
    scheduling_key: str
    commands_json: str
    safe_branch_prefix: str
    allow_commit: bool
    allow_push: bool


@dataclass(frozen=True)
class _TransactionWorkerOutcome:
    """Worker-local transaction result returned to the watcher owner."""

    result: dict[str, Any]
    snapshot: dict[str, Any] | None


@dataclass(frozen=True)
class _WorkerCompletion:
    task_id: int
    outcome: _TransactionWorkerOutcome | None
    error: BaseException | None


@dataclass(frozen=True)
class _WorkerHandle:
    task_id: int
    completion: queue.Queue[Any]


@dataclass(frozen=True)
class _QueuedWorkerTask:
    task_id: int
    task: _TransactionWorkerTask
    completion: queue.Queue[Any]


@dataclass(frozen=True)
class _PendingTransaction:
    """Watcher-owned validated transaction waiting for a worker slot."""

    request: _ValidatedRequest
    request_started: float
    enqueued_at: float
    fair_key: str


@dataclass(frozen=True)
class _InFlightTransaction:
    """Watcher-owned metadata for one submitted local transaction."""

    request: _ValidatedRequest
    handle: _WorkerHandle
    request_started: float
    scheduling_key: str
    enqueued_at: float
    dispatched_at: float


_WORKER_STOP = object()


class _LocalWorkerScheduler:
    """Bounded explicit worker lifecycle used by the B2 one-worker daemon.

    The watcher/main thread owns this scheduler and all mailbox I/O. Worker
    threads receive only immutable local transaction specifications and return
    outcomes over per-task queues. The implementation intentionally avoids
    ``ThreadPoolExecutor`` and Python 3.13+'s ``Queue.shutdown()`` so lifecycle
    semantics remain explicit and compatible with the Python 3.11 minimum.
    """

    def __init__(
        self,
        *,
        max_workers: int = 1,
        queue_capacity: int | None = None,
        worker_fn: Callable[[_TransactionWorkerTask, Callable[[], bool]], _TransactionWorkerOutcome] | None = None,
    ) -> None:
        if not isinstance(max_workers, int) or isinstance(max_workers, bool) or max_workers < 1:
            raise BridgeError("scheduler max_workers must be a positive integer")
        capacity = max_workers if queue_capacity is None else queue_capacity
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            raise BridgeError("scheduler queue_capacity must be a positive integer")
        self.max_workers = max_workers
        self.queue_capacity = capacity
        self._worker_fn = worker_fn or _run_transaction_worker
        self._jobs: queue.Queue[Any] = queue.Queue(maxsize=capacity)
        self._cancel = threading.Event()
        self._threads: list[threading.Thread] = []
        self._owner_thread_id = threading.get_ident()
        self._next_task_id = 1
        self._active_tasks: dict[int, _TransactionWorkerTask] = {}
        self._inflight_repo_keys: set[str] = set()
        self._inflight_txids: set[str] = set()
        self._started = False
        self._closed = False

    def _assert_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise BridgeError("scheduler API must be called by its owner thread")

    def start(self) -> None:
        self._assert_owner()
        if self._closed:
            raise BridgeError("scheduler is already closed")
        if self._started:
            return
        for index in range(self.max_workers):
            thread = threading.Thread(
                target=self._worker_loop,
                name=f"{APP_NAME}-worker-{index + 1}",
                daemon=False,
            )
            thread.start()
            self._threads.append(thread)
        self._started = True

    def _worker_loop(self) -> None:
        while True:
            queued = self._jobs.get()
            try:
                if queued is _WORKER_STOP:
                    return
                assert isinstance(queued, _QueuedWorkerTask)
                try:
                    outcome = self._worker_fn(queued.task, self.cancel_requested)
                except BaseException as exc:
                    completion = _WorkerCompletion(queued.task_id, None, exc)
                else:
                    completion = _WorkerCompletion(queued.task_id, outcome, None)
                queued.completion.put(completion)
            finally:
                self._jobs.task_done()

    @staticmethod
    def _task_txid(task: _TransactionWorkerTask) -> str:
        return task.filename[:-5] if task.filename.endswith(".json") else task.filename

    def active_count(self) -> int:
        self._assert_owner()
        return len(self._active_tasks)

    def repo_in_flight(self, scheduling_key: str) -> bool:
        self._assert_owner()
        return scheduling_key in self._inflight_repo_keys

    def active_repo_keys(self) -> frozenset[str]:
        self._assert_owner()
        return frozenset(self._inflight_repo_keys)

    def active_transaction_ids(self) -> frozenset[str]:
        self._assert_owner()
        return frozenset(self._inflight_txids)

    def submit(self, task: _TransactionWorkerTask) -> _WorkerHandle:
        self._assert_owner()
        if not self._started:
            raise BridgeError("scheduler is not started")
        if self._closed:
            raise BridgeError("scheduler is closed")
        if task.scheduling_key in self._inflight_repo_keys:
            raise BridgeError("repository already has an in-flight transaction")
        txid = self._task_txid(task)
        if txid in self._inflight_txids:
            raise BridgeError("transaction is already in flight")
        task_id = self._next_task_id
        self._next_task_id += 1
        completion: queue.Queue[Any] = queue.Queue(maxsize=1)
        self._jobs.put(_QueuedWorkerTask(task_id, task, completion))
        self._active_tasks[task_id] = task
        self._inflight_repo_keys.add(task.scheduling_key)
        self._inflight_txids.add(txid)
        return _WorkerHandle(task_id, completion)

    def wait(self, handle: _WorkerHandle) -> _TransactionWorkerOutcome:
        self._assert_owner()
        task = self._active_tasks.get(handle.task_id)
        if task is None:
            raise BridgeError("scheduler handle is not active")
        try:
            completion = handle.completion.get()
            if not isinstance(completion, _WorkerCompletion) or completion.task_id != handle.task_id:
                raise BridgeError("scheduler returned an invalid completion")
            if completion.error is not None:
                raise completion.error
            if completion.outcome is None:
                raise BridgeError("scheduler returned no outcome")
            return completion.outcome
        finally:
            self._active_tasks.pop(handle.task_id, None)
            self._inflight_repo_keys.discard(task.scheduling_key)
            self._inflight_txids.discard(self._task_txid(task))

    def execute(self, task: _TransactionWorkerTask) -> _TransactionWorkerOutcome:
        return self.wait(self.submit(task))

    def request_cancel(self) -> None:
        self._cancel.set()

    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def shutdown(self, *, cancel_running: bool = False) -> None:
        self._assert_owner()
        if self._closed:
            return
        if cancel_running:
            self.request_cancel()
        if self._started:
            for _thread in self._threads:
                self._jobs.put(_WORKER_STOP)
            self._jobs.join()
            for thread in self._threads:
                thread.join()
        self._active_tasks.clear()
        self._inflight_repo_keys.clear()
        self._inflight_txids.clear()
        self._closed = True

    def __enter__(self) -> _LocalWorkerScheduler:
        self.start()
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.shutdown(cancel_running=True)


def _quarantine_corrupt_local_result(filename: str, local_result: Path) -> None:
    quarantine_dir = STATE_DIR / "corrupt-results"
    quarantine_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(quarantine_dir, 0o700)
    except OSError:
        pass
    target = quarantine_dir / filename
    try:
        os.replace(local_result, target)
    except OSError as exc:
        raise BridgeError(f"cannot quarantine corrupt local result: {type(exc).__name__}") from exc
    _append_metric({
        "event": "local-result-quarantined",
        "transaction_id": filename[:-5],
        "recorded_at": utc_now(),
    })


def _publish_local_result(
    transport: RcloneTransport,
    filename: str,
    local_result: Path,
) -> float | None:
    # A previous upload may have timed out after Drive accepted the write. Resolve
    # that ambiguous outcome by checking the result directory before attempting a
    # second mutating upload, which could otherwise create a duplicate Drive file.
    # Check the authenticated remote acknowledgement before parsing the local copy:
    # a torn local file must not mask an acknowledgement that Drive already accepted.
    remote_results = transport.list_files(f"{REMOTE_ROOT}/results")
    if filename in remote_results:
        remote_result = _load_authentic_remote_result(transport, filename)
        if remote_result is None:
            raise BridgeError("conflicting unauthenticated remote result")
        _mark_published(
            filename,
            source="remote-result-existing",
            request_bytes_sha256=remote_result.get("request_bytes_sha256"),
        )
        cleanup_status, cleanup_s, cleanup_transport = _cleanup_remote_request(transport, filename)
        _cleanup_local_request_artifacts(filename)
        _prune_local_results()
        _prune_command_logs()
        _append_metric({
            "event": "result-republish",
            "transaction_id": filename[:-5],
            "result_upload_s": 0.0,
            "request_cleanup_status": cleanup_status,
            "request_cleanup_s": cleanup_s,
            "request_cleanup_transport": cleanup_transport,
            "recorded_at": utc_now(),
        })
        return 0.0

    try:
        result = load_json(local_result)
    except BridgeError as exc:
        _quarantine_corrupt_local_result(filename, local_result)
        raise _CorruptLocalResult("corrupt local durable result quarantined") from exc
    if not _result_identity_is_valid(filename, result) or not _verify_result(filename, result):
        _quarantine_corrupt_local_result(filename, local_result)
        raise _CorruptLocalResult("local durable result authentication is invalid")

    try:
        delta = time.time() - local_result.stat().st_mtime
        age = RESULT_RETRY_GRACE_S if delta < -RESULT_RETRY_GRACE_S else max(0.0, delta)
    except OSError:
        age = RESULT_RETRY_GRACE_S
    if age < RESULT_RETRY_GRACE_S:
        # Give an in-flight/timed-out provider write time to become visible.
        return None

    started = time.monotonic()
    transport.upload_control_json(
        f"{REMOTE_ROOT}/results/{filename}",
        result,
        STATE_DIR / "outbox" / f"result-{filename}",
    )
    elapsed = round(time.monotonic() - started, 4)
    _mark_published(filename, source="local-result", request_bytes_sha256=result.get("request_bytes_sha256"))
    cleanup_status, cleanup_s, cleanup_transport = _cleanup_remote_request(transport, filename)
    _cleanup_local_request_artifacts(filename)
    _prune_local_results()
    _prune_command_logs()
    _append_metric({
        "event": "result-republish",
        "transaction_id": filename[:-5],
        "result_upload_s": elapsed,
        "request_cleanup_status": cleanup_status,
        "request_cleanup_s": cleanup_s,
        "request_cleanup_transport": cleanup_transport,
        "recorded_at": utc_now(),
    })
    return elapsed


def reconcile_remote_results(cfg: dict[str, Any]) -> int:
    """Recover replay markers only from authenticated remote acknowledgements.

    The mailbox writer can also write the result directory, so a filename alone
    is not evidence that the daemon processed a request. At startup we verify only
    result objects whose transaction is still present in the inbox. This keeps the
    normal hot path unchanged and avoids downloading the historical result archive.
    """
    transport = transport_from_config(cfg)
    pending_transactions = {
        name
        for name in transport.list_files(f"{REMOTE_ROOT}/transactions")
        if name.endswith(".json") and TX_FILENAME_RE.fullmatch(name)
    }
    unmarked_transactions = {
        name for name in pending_transactions if _load_published_marker(name) is None
    }
    if not unmarked_transactions:
        _append_metric({
            "event": "startup-reconcile",
            "remote_results": 0,
            "markers_added": 0,
            "results_list_s": 0.0,
            "recorded_at": utc_now(),
        })
        return 0

    started = time.monotonic()
    remote_results = {
        name
        for name in transport.list_files(f"{REMOTE_ROOT}/results")
        if name.endswith(".json") and TX_FILENAME_RE.fullmatch(name)
    }
    list_elapsed = round(time.monotonic() - started, 4)
    marked = 0
    candidates = sorted(remote_results & unmarked_transactions)
    for filename in candidates:
        remote_result = _load_authentic_remote_result(transport, filename)
        if remote_result is None:
            continue
        _mark_published(
            filename,
            source="startup-authenticated-remote-result",
            request_bytes_sha256=remote_result.get("request_bytes_sha256"),
        )
        marked += 1
    _append_metric({
        "event": "startup-reconcile",
        "remote_results": len(remote_results),
        "markers_added": marked,
        "results_list_s": list_elapsed,
        "recorded_at": utc_now(),
    })
    return marked


def _poll_transaction_mailbox(transport: RcloneTransport) -> _TransactionPoll:
    """List the inbox once and freeze the entries used by this poll."""
    started = time.monotonic()
    entries = tuple(
        entry
        for entry in transport.list_entries(f"{REMOTE_ROOT}/transactions")
        if entry.name.endswith(".json") and TX_FILENAME_RE.fullmatch(entry.name)
    )
    return _TransactionPoll(
        entries=entries,
        list_s=round(time.monotonic() - started, 4),
        list_transport=str(getattr(transport, "last_mode", "unknown")),
    )


def _classify_transaction_mailbox(
    transport: RcloneTransport,
    poll: _TransactionPoll,
) -> _MailboxClassification:
    """Separate executable requests from durable acknowledgements.

    Invalid local publication markers are repaired only from authenticated remote
    results; otherwise their requests remain executable. This preserves the A4
    replay semantics while exposing a narrow scheduler-facing classification stage.
    """
    candidates: list[str] = []
    published: list[str] = []
    invalid_marker_names: list[str] = []
    for name in poll.filenames:
        marker_path = _published_marker(name)
        marker_existed = marker_path.exists()
        if _load_published_marker(name) is not None:
            published.append(name)
        else:
            candidates.append(name)
            if marker_existed:
                invalid_marker_names.append(name)

    if invalid_marker_names:
        remote_results = set(transport.list_files(f"{REMOTE_ROOT}/results"))
        for name in invalid_marker_names:
            if name not in remote_results:
                continue
            remote_result = _load_authentic_remote_result(transport, name)
            if remote_result is None:
                continue
            _mark_published(
                name,
                source="invalid-marker-authenticated-remote-result",
                request_bytes_sha256=remote_result.get("request_bytes_sha256"),
            )
            if name in candidates:
                candidates.remove(name)
            published.append(name)

    return _MailboxClassification(tuple(candidates), tuple(published))


def _idle_acknowledgement_maintenance(
    transport: RcloneTransport,
    published: tuple[str, ...],
) -> None:
    """Retire at most one acknowledged inbox object during an idle poll."""
    if not published:
        return
    acknowledged = published[0]
    _cleanup_remote_request(transport, acknowledged, allow_fallback=False)
    _cleanup_local_request_artifacts(acknowledged)
    _prune_local_results()
    _prune_command_logs()


def _recover_durable_local_results(
    transport: RcloneTransport,
    candidates: tuple[str, ...],
) -> tuple[int, tuple[str, ...]]:
    """Publish durable local results before allowing any request to re-execute."""
    processed = 0
    still_pending: list[str] = []
    for filename in candidates:
        local_result = STATE_DIR / "results" / filename
        if local_result.exists():
            try:
                if _publish_local_result(transport, filename, local_result) is not None:
                    processed += 1
            except _CorruptLocalResult:
                # The durable copy is unusable, but the remote request still exists.
                # Re-enter the ordinary request state machine. Git transactions are
                # protected against duplicate mutation by request-hash commit trailers.
                still_pending.append(filename)
        else:
            still_pending.append(filename)
    return processed, tuple(still_pending)


def _ensure_registry_for_dispatch(cfg: dict[str, Any]) -> dict[str, Any]:
    if not REGISTRY_FILE.exists():
        return refresh_registry(cfg, publish=True)
    return load_registry()


def _download_remote_request(
    transport: RcloneTransport,
    filename: str,
    *,
    reported_size: int | None,
    poll: _TransactionPoll,
    poll_started: float,
) -> _DownloadedRequest | None:
    """Download exact request text, returning None only for transient transport failure."""
    if reported_size is not None and reported_size > MAX_REQUEST_BYTES:
        raise BridgeError("remote request exceeds maximum allowed size")

    tx_local = STATE_DIR / "inbox" / filename
    started = time.monotonic()
    try:
        raw = transport.download_text(
            f"{REMOTE_ROOT}/transactions/{filename}",
            tx_local,
            max_bytes=MAX_REQUEST_BYTES,
        )
    except TransientTransportError:
        download_s = round(time.monotonic() - started, 4)
        download_transport = str(getattr(transport, "last_mode", "unknown"))
        tx_local.unlink(missing_ok=True)
        _append_metric({
            "event": "transaction-download-retry",
            "transaction_id": filename[:-5],
            "transaction_list_s": poll.list_s,
            "transaction_download_s": download_s,
            "transaction_list_transport": poll.list_transport,
            "transaction_download_transport": download_transport,
            "poll_total_s": round(time.monotonic() - poll_started, 4),
            "recorded_at": utc_now(),
        })
        return None

    return _DownloadedRequest(
        filename=filename,
        raw=raw,
        request_bytes_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        download_s=round(time.monotonic() - started, 4),
        download_transport=str(getattr(transport, "last_mode", "unknown")),
    )


def _validate_downloaded_request(downloaded: _DownloadedRequest) -> _ValidatedRequest:
    obj = strict_json_loads(downloaded.raw.lstrip("\ufeff"))
    if not isinstance(obj, dict):
        raise BridgeError("request JSON must be an object")
    _validate_request_identity(obj, downloaded.filename)
    return _ValidatedRequest(downloaded=downloaded, obj=obj)


def _plan_local_job(
    registry: dict[str, Any],
    request: _ValidatedRequest,
) -> _LocalJob:
    """Classify a validated envelope without changing execution semantics.

    Valid repository references are canonicalised to the registry repository ID so
    B2 can use one mutual-exclusion domain even when clients use different accepted
    references. Invalid/missing references are left unkeyed and will fail through
    the existing execution validation path before any repository mutation.
    """
    kind = request.obj.get("kind")
    if kind in {"transaction", "materialize"}:
        repo_ref = request.obj.get("repo")
        scheduling_key: str | None = None
        if isinstance(repo_ref, str) and repo_ref.strip():
            repo_id, _entry = resolve_repo(registry, repo_ref)
            scheduling_key = repo_id
        return _LocalJob(
            request=request,
            kind=str(kind),
            resource_class="repository",
            scheduling_key=scheduling_key,
        )
    if kind in {"doctor", "diagnostics"}:
        return _LocalJob(
            request=request,
            kind=str(kind),
            resource_class="control",
            scheduling_key=None,
        )
    return _LocalJob(
        request=request,
        kind=str(kind),
        resource_class="invalid",
        scheduling_key=None,
    )


def _prepare_transaction_worker_task(
    cfg: dict[str, Any],
    registry: dict[str, Any],
    request: _ValidatedRequest,
) -> _TransactionWorkerTask:
    """Freeze all transaction inputs before handing work to a worker thread."""
    obj = request.obj
    repo_id, entry = resolve_repo(registry, obj.get("repo", ""))
    commands_cfg = cfg.get("commands", {}).get(repo_id, {})
    commands_json = json.dumps(commands_cfg, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _TransactionWorkerTask(
        filename=request.downloaded.filename,
        request_raw=request.downloaded.raw,
        repo_id=repo_id,
        repo_path=Path(entry["path"]),
        scheduling_key=str(Path(entry["path"]).expanduser().resolve()),
        commands_json=commands_json,
        safe_branch_prefix=str(cfg.get("safe_branch_prefix", "ai/")),
        allow_commit=bool(cfg.get("allow_commit", True)),
        allow_push=repo_id in {str(x) for x in cfg.get("push_enabled_repos", [])},
    )


def _run_transaction_worker(
    task: _TransactionWorkerTask,
    cancel_check: Callable[[], bool],
) -> _TransactionWorkerOutcome:
    """Execute only local Git/command work; never perform mailbox transport I/O."""
    obj = strict_json_loads(task.request_raw.lstrip("\ufeff"))
    if not isinstance(obj, dict):
        raise BridgeError("request JSON must be an object")
    _validate_request_identity(obj, task.filename)
    outcome = process_transaction(
        task.repo_path,
        task.repo_id,
        obj,
        state_dir=STATE_DIR,
        safe_branch_prefix=task.safe_branch_prefix,
        commands=json.loads(task.commands_json),
        allow_commit=task.allow_commit,
        allow_push=task.allow_push,
        cancel_check=cancel_check,
    )
    return _TransactionWorkerOutcome(dict(outcome.result), outcome.snapshot)


def _publish_transaction_snapshot(
    transport: RcloneTransport,
    request: _ValidatedRequest,
    outcome: _TransactionWorkerOutcome,
) -> dict[str, Any]:
    """Watcher-owned optional snapshot publication after local worker completion."""
    result = dict(outcome.result)
    if outcome.snapshot is None or not request.obj.get("publish_snapshot", False):
        return result
    branch = result["branch"]
    repo_id = result["repo"]
    snap_rel = f"{REMOTE_ROOT}/repos/{repo_id}/branches/{branch_token(branch)}/snapshot.json"
    try:
        transport.upload_json(
            snap_rel,
            outcome.snapshot,
            STATE_DIR / "outbox" / f"snapshot-{repo_id}-{result['transaction_id']}.json",
        )
        result["snapshot"] = snap_rel
    except Exception:
        result["snapshot_error"] = "snapshot publication failed"
    return result


def _execute_validated_request(
    cfg: dict[str, Any],
    transport: RcloneTransport,
    registry: dict[str, Any],
    request: _ValidatedRequest,
    *,
    cancel_check: Callable[[], bool] | None,
    scheduler: _LocalWorkerScheduler | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Execute one validated request synchronously and return updated registry state."""
    obj = request.obj
    filename = request.downloaded.filename
    kind = obj.get("kind")

    # Re-read the atomically-written registry immediately before repo-dependent
    # dispatch so a concurrent local scan cannot leave queued work using stale paths.
    if kind in {"materialize", "transaction"}:
        registry = load_registry()

    if kind == "materialize":
        result = _process_materialize_request(cfg, registry, obj, filename)
        registry = load_registry()
    elif kind == "diagnostics":
        result = _process_diagnostics_request(obj, filename)
    elif kind == "doctor":
        result = _process_doctor_request(cfg, obj, filename)
    elif kind == "transaction":
        worker_task = _prepare_transaction_worker_task(cfg, registry, request)
        if scheduler is None:
            worker_outcome = _run_transaction_worker(worker_task, cancel_check or (lambda: False))
        else:
            worker_outcome = scheduler.execute(worker_task)
        result = _publish_transaction_snapshot(transport, request, worker_outcome)
    else:
        raise BridgeError(f"unsupported request kind: {kind!r}")
    return result, registry


def _build_signed_result(
    filename: str,
    result: dict[str, Any],
    *,
    downloaded: _DownloadedRequest | None,
    poll: _TransactionPoll,
    request_started: float,
) -> dict[str, Any]:
    """Bind execution outcome to exact input bytes and authenticate it."""
    result = dict(result)
    if downloaded is not None:
        result["request_bytes_sha256"] = downloaded.request_bytes_sha256
        download_s = downloaded.download_s
        download_transport = downloaded.download_transport
    else:
        download_s = 0.0
        download_transport = "none"
    result["transport_timings"] = {
        "transaction_list_s": poll.list_s,
        "transaction_download_s": download_s,
        "transaction_list_transport": poll.list_transport,
        "transaction_download_transport": download_transport,
        "pre_result_upload_s": round(time.monotonic() - request_started, 4),
    }
    return _sign_result(filename, result)


def _persist_signed_result(filename: str, result: dict[str, Any]) -> Path:
    """Persist the signed acknowledgement before any remote publication attempt."""
    local_result = STATE_DIR / "results" / filename
    save_json(local_result, result)
    return local_result


def _publish_and_cleanup_result(
    transport: RcloneTransport,
    filename: str,
    result: dict[str, Any],
    *,
    poll: _TransactionPoll,
    poll_started: float,
    request_started: float,
    protected_command_log_txids: Collection[str] = (),
) -> None:
    """Publish a durable result, acknowledge the request, then retire transient state."""
    upload_started = time.monotonic()
    transport.upload_control_json(
        f"{REMOTE_ROOT}/results/{filename}",
        result,
        STATE_DIR / "outbox" / f"result-{filename}",
    )
    result_upload_s = round(time.monotonic() - upload_started, 4)
    result_upload_transport = str(getattr(transport, "last_mode", "unknown"))
    _mark_published(filename, source="processed", request_bytes_sha256=result.get("request_bytes_sha256"))
    cleanup_status, cleanup_s, cleanup_transport = _cleanup_remote_request(transport, filename)
    _cleanup_local_request_artifacts(filename)
    _prune_local_results()
    _prune_command_logs(protected_txids=protected_command_log_txids)
    timings = result["transport_timings"]
    _append_metric({
        "event": "transaction",
        "transaction_id": filename[:-5],
        "transaction_list_s": poll.list_s,
        "transaction_download_s": timings["transaction_download_s"],
        "pre_result_upload_s": timings["pre_result_upload_s"],
        "result_upload_s": result_upload_s,
        "transaction_list_transport": poll.list_transport,
        "transaction_download_transport": timings["transaction_download_transport"],
        "result_upload_transport": result_upload_transport,
        "request_cleanup_status": cleanup_status,
        "request_cleanup_s": cleanup_s,
        "request_cleanup_transport": cleanup_transport,
        "request_total_s": round(time.monotonic() - request_started, 4),
        "poll_total_s": round(time.monotonic() - poll_started, 4),
        "recorded_at": utc_now(),
    })


def _finalize_request_result(
    transport: RcloneTransport,
    filename: str,
    result: dict[str, Any],
    *,
    downloaded: _DownloadedRequest | None,
    poll: _TransactionPoll,
    poll_started: float,
    request_started: float,
    protected_command_log_txids: Collection[str] = (),
) -> None:
    signed = _build_signed_result(
        filename,
        result,
        downloaded=downloaded,
        poll=poll,
        request_started=request_started,
    )
    _persist_signed_result(filename, signed)
    _publish_and_cleanup_result(
        transport,
        filename,
        signed,
        poll=poll,
        poll_started=poll_started,
        request_started=request_started,
        protected_command_log_txids=protected_command_log_txids,
    )


def _process_pending_concurrent(
    cfg: dict[str, Any],
    transport: RcloneTransport,
    poll: _TransactionPoll,
    poll_started: float,
    still_pending: tuple[str, ...],
    registry: dict[str, Any],
    scheduler: _LocalWorkerScheduler,
    *,
    cancel_check: Callable[[], bool] | None,
) -> int:
    """Execute independent repository transactions concurrently.

    The watcher owns discovery, validation, admission, transport and publication.
    Validated transactions may wait in a bounded watcher-owned queue so a burst for
    repository A cannot force the watcher to block before discovering runnable work
    for repository B. Worker admission is re-resolved immediately before dispatch,
    preserving the registry-change fail-closed invariant.
    """
    active: list[_InFlightTransaction] = []
    pending: list[_PendingTransaction] = []
    fair_order: list[str] = []
    fair_cursor = 0
    processed = 0
    max_pending_jobs = int(cfg.get("max_pending_jobs", 8))

    def scheduler_metric(event: str, *, queue_wait_s: float | None = None, execution_s: float | None = None) -> None:
        active_workers = scheduler.active_count()
        metric: dict[str, Any] = {
            "event": event,
            "queue_depth": len(pending),
            "active_workers": active_workers,
            "active_repos": len(scheduler.active_repo_keys()),
            "scheduler_utilization": round(active_workers / scheduler.max_workers, 4),
            "recorded_at": utc_now(),
        }
        if queue_wait_s is not None:
            metric["queue_wait_s"] = round(queue_wait_s, 4)
        if execution_s is not None:
            metric["execution_s"] = round(execution_s, 4)
        _append_metric(metric)

    def finish_inflight(item: _InFlightTransaction) -> None:
        nonlocal registry
        request = item.request
        filename = request.downloaded.filename
        try:
            worker_outcome = scheduler.wait(item.handle)
            result = _publish_transaction_snapshot(transport, request, worker_outcome)
        except Exception as exc:
            result = {
                "protocol": PROTOCOL_VERSION,
                "kind": "result",
                "transaction_id": filename[:-5],
                "status": "error",
                "processed_at": utc_now(),
                "error": _public_error_message(exc),
            }
        execution_s = time.monotonic() - item.dispatched_at
        scheduler_metric("scheduler-finish", execution_s=execution_s)
        _finalize_request_result(
            transport,
            filename,
            result,
            downloaded=request.downloaded,
            poll=poll,
            poll_started=poll_started,
            request_started=item.request_started,
            protected_command_log_txids=scheduler.active_transaction_ids(),
        )
        registry = load_registry()

    def drain_item(item: _InFlightTransaction) -> None:
        nonlocal processed
        active.remove(item)
        finish_inflight(item)
        processed += 1

    def dispatch_pending() -> int:
        """Dispatch runnable queued jobs using round-robin repository fairness."""
        nonlocal registry, processed, fair_cursor
        dispatched = 0
        while pending and scheduler.active_count() < scheduler.max_workers:
            runnable: dict[str, tuple[int, _TransactionWorkerTask, dict[str, Any]]] = {}
            chosen_error: tuple[int, Exception] | None = None
            # Only the first queued request for each repository is eligible. The
            # worker task is rebuilt from the latest registry immediately before
            # handoff so fairness state can never override fail-closed routing.
            seen_fair_keys: set[str] = set()
            for index, item in enumerate(pending):
                if item.fair_key in seen_fair_keys:
                    continue
                seen_fair_keys.add(item.fair_key)
                try:
                    latest_registry = load_registry()
                    candidate = _prepare_transaction_worker_task(cfg, latest_registry, item.request)
                except Exception as exc:
                    chosen_error = (index, exc)
                    break
                if scheduler.repo_in_flight(candidate.scheduling_key):
                    continue
                runnable[item.fair_key] = (index, candidate, latest_registry)

            chosen_index: int | None = None
            chosen_task: _TransactionWorkerTask | None = None
            chosen_error_exc: Exception | None = None
            if chosen_error is not None:
                chosen_index, chosen_error_exc = chosen_error
            elif runnable:
                if not fair_order:
                    raise BridgeError("scheduler fairness state is inconsistent")
                for offset in range(len(fair_order)):
                    pos = (fair_cursor + offset) % len(fair_order)
                    key = fair_order[pos]
                    selected = runnable.get(key)
                    if selected is None:
                        continue
                    chosen_index, chosen_task, latest_registry = selected
                    registry = latest_registry
                    fair_cursor = (pos + 1) % len(fair_order)
                    break
            if chosen_index is None:
                break
            item = pending.pop(chosen_index)
            if chosen_error_exc is not None:
                filename = item.request.downloaded.filename
                result = {
                    "protocol": PROTOCOL_VERSION,
                    "kind": "result",
                    "transaction_id": filename[:-5],
                    "status": "error",
                    "processed_at": utc_now(),
                    "error": _public_error_message(chosen_error_exc),
                }
                _finalize_request_result(
                    transport,
                    filename,
                    result,
                    downloaded=item.request.downloaded,
                    poll=poll,
                    poll_started=poll_started,
                    request_started=item.request_started,
                    protected_command_log_txids=scheduler.active_transaction_ids(),
                )
                processed += 1
                continue
            assert chosen_task is not None
            dispatched_at = time.monotonic()
            handle = scheduler.submit(chosen_task)
            active.append(_InFlightTransaction(
                request=item.request,
                handle=handle,
                request_started=item.request_started,
                scheduling_key=chosen_task.scheduling_key,
                enqueued_at=item.enqueued_at,
                dispatched_at=dispatched_at,
            ))
            scheduler_metric("scheduler-dispatch", queue_wait_s=dispatched_at - item.enqueued_at)
            dispatched += 1
        return dispatched

    def drain_one() -> None:
        if not active:
            raise BridgeError("scheduler admission state is inconsistent")
        drain_item(active[0])
        dispatch_pending()

    def drain_all() -> None:
        while pending or active:
            dispatch_pending()
            if pending and not active:
                raise BridgeError("scheduler pending queue cannot make progress")
            if active:
                drain_one()

    try:
        for filename in still_pending:
            # Bound validated-but-not-finished work before downloading another
            # untrusted request. This bounds memory even under mailbox floods.
            while len(pending) + len(active) >= max_pending_jobs:
                dispatch_pending()
                if len(pending) + len(active) < max_pending_jobs:
                    break
                drain_one()

            request_started = time.monotonic()
            downloaded: _DownloadedRequest | None = None
            try:
                downloaded = _download_remote_request(
                    transport,
                    filename,
                    reported_size=poll.size_for(filename),
                    poll=poll,
                    poll_started=poll_started,
                )
                if downloaded is None:
                    continue
                request = _validate_downloaded_request(downloaded)
                job = _plan_local_job(registry, request)

                if job.kind == "transaction":
                    enqueued_at = time.monotonic()
                    fair_key = job.scheduling_key or request.downloaded.filename
                    if fair_key not in fair_order:
                        fair_order.append(fair_key)
                    pending.append(_PendingTransaction(request, request_started, enqueued_at, fair_key))
                    scheduler_metric("scheduler-enqueue")
                    dispatch_pending()
                    continue

                if job.kind == "materialize":
                    # Materialisation remains watcher-owned and is a strict barrier:
                    # all earlier local transactions finish and publish first.
                    drain_all()

                if job.kind == "diagnostics":
                    result = _process_diagnostics_request(
                        request.obj,
                        request.downloaded.filename,
                        scheduler_status={
                            "max_workers": scheduler.max_workers,
                            "active_workers": scheduler.active_count(),
                            "active_repos": len(scheduler.active_repo_keys()),
                            "queue_depth": len(pending),
                            "max_pending_jobs": max_pending_jobs,
                        },
                    )
                else:
                    result, registry = _execute_validated_request(
                        cfg,
                        transport,
                        registry,
                        request,
                        cancel_check=cancel_check,
                        scheduler=None,
                    )
            except Exception as exc:
                result = {
                    "protocol": PROTOCOL_VERSION,
                    "kind": "result",
                    "transaction_id": filename[:-5],
                    "status": "error",
                    "processed_at": utc_now(),
                    "error": _public_error_message(exc),
                }

            _finalize_request_result(
                transport,
                filename,
                result,
                downloaded=downloaded,
                poll=poll,
                poll_started=poll_started,
                request_started=request_started,
                protected_command_log_txids=scheduler.active_transaction_ids(),
            )
            processed += 1

        drain_all()
        return processed
    except BaseException:
        # Reap every submitted worker before unwinding. Pending requests were never
        # handed to a worker and remain remotely durable for the next poll/restart.
        pending.clear()
        while active:
            item = active[0]
            try:
                drain_item(item)
            except BaseException:
                continue
        raise


def process_pending_once(
    cfg: dict[str, Any],
    *,
    cancel_check: Callable[[], bool] | None = None,
    scheduler: _LocalWorkerScheduler | None = None,
) -> int:
    """Run one synchronous mailbox cycle through explicit durable stages."""
    transport = transport_from_config(cfg)
    poll_started = time.monotonic()
    poll = _poll_transaction_mailbox(transport)
    classified = _classify_transaction_mailbox(transport, poll)

    if not classified.candidates:
        _idle_acknowledgement_maintenance(transport, classified.published)
        return 0

    processed, still_pending = _recover_durable_local_results(transport, classified.candidates)
    if not still_pending:
        return processed

    registry = _ensure_registry_for_dispatch(cfg)
    if scheduler is not None and scheduler.max_workers > 1:
        return processed + _process_pending_concurrent(
            cfg,
            transport,
            poll,
            poll_started,
            still_pending,
            registry,
            scheduler,
            cancel_check=cancel_check,
        )

    for filename in still_pending:
        request_started = time.monotonic()
        downloaded: _DownloadedRequest | None = None
        try:
            downloaded = _download_remote_request(
                transport,
                filename,
                reported_size=poll.size_for(filename),
                poll=poll,
                poll_started=poll_started,
            )
            if downloaded is None:
                # A transient provider failure is not terminal application state.
                continue
            request = _validate_downloaded_request(downloaded)
            job = _plan_local_job(registry, request)
            result, registry = _execute_validated_request(
                cfg,
                transport,
                registry,
                job.request,
                cancel_check=cancel_check,
                scheduler=scheduler,
            )
        except Exception as exc:
            result = {
                "protocol": PROTOCOL_VERSION,
                "kind": "result",
                "transaction_id": filename[:-5],
                "status": "error",
                "processed_at": utc_now(),
                "error": _public_error_message(exc),
            }

        result = _build_signed_result(
            filename,
            result,
            downloaded=downloaded,
            poll=poll,
            request_started=request_started,
        )
        _persist_signed_result(filename, result)
        _publish_and_cleanup_result(
            transport,
            filename,
            result,
            poll=poll,
            poll_started=poll_started,
            request_started=request_started,
        )
        processed += 1
    return processed


def cmd_setup(args: argparse.Namespace) -> int:
    cfg = load_config()
    remote = args.remote or cfg.get("transport", {}).get("remote") or detect_rclone_remote()
    transport_cfg = cfg.setdefault("transport", {})
    transport_cfg.update({"type": "rclone", "remote": remote.rstrip(":")})
    transport_cfg.setdefault("rc_enabled", True)
    save_config(cfg)
    transport = transport_from_config(cfg)
    for rel in ("meta", "repos", "transactions", "results"):
        transport.ensure_dir(f"{REMOTE_ROOT}/{rel}")
    print(f"configured rclone remote: {cfg['transport']['remote']}:")
    return 0


def cmd_add_root(args: argparse.Namespace) -> int:
    cfg = load_config()
    root = str(Path(args.path).expanduser().resolve())
    roots = [str(Path(x).expanduser().resolve()) for x in cfg.get("roots", [])]
    if root not in roots:
        roots.append(root)
    cfg["roots"] = sorted(set(roots))
    save_config(cfg)
    registry = refresh_registry(cfg, publish=True)
    print(f"registered roots: {len(cfg['roots'])}; repositories: {len(registry.get('repos', {}))}")
    return 0


def cmd_scan(_args: argparse.Namespace) -> int:
    cfg = load_config()
    registry = refresh_registry(cfg, publish=True)
    print(f"repositories: {len(registry.get('repos', {}))}")
    return 0


def cmd_materialize(args: argparse.Namespace) -> int:
    cfg = load_config()
    if not REGISTRY_FILE.exists():
        refresh_registry(cfg, publish=True)
    else:
        refresh_repo_entry(cfg, args.repo, publish=True)
    remote_rel = materialize(cfg, args.repo)
    print(remote_rel)
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    cfg = load_config()
    print(f"config: {CONFIG_FILE}")
    print(f"state: {STATE_DIR}")
    print(f"remote: {cfg.get('transport', {}).get('remote') or '(not configured)'}")
    print(f"roots: {len(cfg.get('roots', []))}")
    print(
        f"concurrency: {int(cfg.get('max_workers', 1))} workers; "
        f"{int(cfg.get('max_pending_jobs', 8))} max pending jobs"
    )
    if REGISTRY_FILE.exists():
        reg = load_registry()
        repos = reg.get("repos", {})
        dirty = sum(1 for x in repos.values() if x.get("dirty"))
        print(f"repositories: {len(repos)} ({dirty} dirty)")
    else:
        print("repositories: registry not built")
    print(f"launch agent: {'installed' if PLIST_PATH.exists() else 'not installed'}")
    return 0


def _acquire_watch_lock():
    """Serialize mailbox consumers so one request cannot run twice concurrently."""
    import fcntl

    path = STATE_DIR / "watch.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        fh.close()
        raise BridgeError("another bridge watcher is already running") from exc
    return fh


def _release_watch_lock(fh) -> None:
    import fcntl

    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


def _start_watch_rcd(cfg: dict[str, Any]) -> RcloneRCProcess | None:
    transport = cfg.get("transport", {})
    if transport.get("type") != "rclone" or not bool(transport.get("rc_enabled", True)):
        return None
    handle = start_rclone_rcd(RCLONE_RC_SOCKET)
    if handle is None:
        print("rclone rcd unavailable; using subprocess fallback", file=sys.stderr, flush=True)
    else:
        print("rclone rcd ready", flush=True)
    return handle


def _ensure_watch_rcd(cfg: dict[str, Any], handle: RcloneRCProcess | None) -> RcloneRCProcess | None:
    transport = cfg.get("transport", {})
    enabled = transport.get("type") == "rclone" and bool(transport.get("rc_enabled", True))
    if not enabled:
        if handle is not None and handle.owned:
            handle.stop()
        return None

    if handle is not None:
        process_dead = handle.process is not None and handle.process.poll() is not None
        socket_missing = not handle.socket_path.exists()
        if not process_dead and not socket_missing:
            if handle.healthy(timeout=RCD_HEALTH_TIMEOUT_S):
                handle.health_failures = 0
                return handle
            handle.health_failures += 1
            _append_metric({
                "event": "rcd-health-miss",
                "consecutive_failures": handle.health_failures,
                "recorded_at": utc_now(),
            })
            if handle.health_failures < RCD_HEALTH_FAILURE_THRESHOLD:
                return handle

        if handle.owned:
            handle.stop()

    restarted = start_rclone_rcd(RCLONE_RC_SOCKET)
    if restarted is not None:
        _append_metric({"event": "rcd-restart", "recorded_at": utc_now()})
        print("rclone rcd restarted", flush=True)
    return restarted


def cmd_watch(args: argparse.Namespace) -> int:
    lock_fh = _acquire_watch_lock()
    rc_handle: RcloneRCProcess | None = None
    scheduler: _LocalWorkerScheduler | None = None
    try:
        cfg = load_config()
        rc_handle = _start_watch_rcd(cfg)
        max_workers = int(cfg.get("max_workers", 1))
        scheduler = _LocalWorkerScheduler(max_workers=max_workers, queue_capacity=max_workers)
        scheduler.start()
        if args.once:
            reconcile_remote_results(cfg)
            reconcile_local_acknowledged_artifacts()
            count = process_pending_once(cfg, scheduler=scheduler)
            print(f"processed: {count}")
            return 0
        interval_value = args.interval if args.interval is not None else cfg.get("poll_interval", 1.0)
        interval = _validate_poll_interval(interval_value)
        print(f"watching every {interval:.1f}s; Ctrl-C to stop", flush=True)
        stop = False
        reconciled = False

        def handle_stop(_sig: int, _frame: Any) -> None:
            nonlocal stop
            stop = True
            if scheduler is not None:
                scheduler.request_cancel()

        signal.signal(signal.SIGTERM, handle_stop)
        signal.signal(signal.SIGINT, handle_stop)
        while not stop:
            try:
                # Fail closed until the one-time remote-result reconciliation succeeds.
                # This preserves replay safety after local-state loss without charging
                # every newly observed transaction for an extra Drive directory listing.
                current_cfg = load_config()
                rc_handle = _ensure_watch_rcd(current_cfg, rc_handle)
                if not reconciled:
                    marked = reconcile_remote_results(current_cfg)
                    cleaned = reconcile_local_acknowledged_artifacts()
                    reconciled = True
                    if marked or cleaned:
                        print(
                            f"startup reconciliation: {marked} marker(s), {cleaned} local cleanup(s)",
                            flush=True,
                        )
                count = process_pending_once(
                    current_cfg,
                    cancel_check=lambda: stop,
                    scheduler=scheduler,
                )
                if count:
                    print(f"processed: {count}", flush=True)
            except Exception as exc:
                print(f"watch error: {_public_error_message(exc)}", file=sys.stderr, flush=True)
            deadline = time.monotonic() + interval
            while not stop and time.monotonic() < deadline:
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        return 0
    finally:
        if scheduler is not None:
            scheduler.shutdown(cancel_running=True)
        if rc_handle is not None:
            rc_handle.stop()
        _release_watch_lock(lock_fh)


def _launchctl(*args: str, check: bool = True) -> int:
    from .core import run

    proc = run(["launchctl", *args], check=False, timeout=30)
    if check and proc.returncode != 0:
        raise BridgeError((proc.stderr or proc.stdout).strip() or f"launchctl {' '.join(args)} failed")
    return proc.returncode


def cmd_daemon(args: argparse.Namespace) -> int:
    if sys.platform != "darwin":
        raise BridgeError("LaunchAgent management is supported only on macOS")
    wrapper = Path(args.command).expanduser().resolve() if getattr(args, "command", None) else None
    domain = f"gui/{os.getuid()}"
    if args.action == "install":
        if wrapper is None or not wrapper.exists():
            raise BridgeError("--command must point to the llm-git-bridge wrapper")
        rclone = shutil.which("rclone")
        if not rclone:
            raise BridgeError("rclone is not available while installing the LaunchAgent")
        PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        current_path = os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
        path_parts = [str(Path(rclone).parent), "/opt/homebrew/bin", "/usr/local/bin", current_path]
        daemon_path = ":".join(dict.fromkeys(part for chunk in path_parts for part in chunk.split(":") if part))
        plist = {
            "Label": LABEL,
            "ProgramArguments": [str(wrapper), "watch"],
            "WorkingDirectory": str(wrapper.parent.parent),
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 5,
            "ProcessType": "Background",
            "EnvironmentVariables": {"PATH": daemon_path},
            "StandardOutPath": str(STATE_DIR / "daemon.out.log"),
            "StandardErrorPath": str(STATE_DIR / "daemon.err.log"),
        }
        with PLIST_PATH.open("wb") as fh:
            plistlib.dump(plist, fh, sort_keys=True)
        _launchctl("bootout", domain, str(PLIST_PATH), check=False)
        _launchctl("bootstrap", domain, str(PLIST_PATH))
        print(f"installed: {PLIST_PATH}")
        return 0
    if args.action == "uninstall":
        _launchctl("bootout", domain, str(PLIST_PATH), check=False)
        PLIST_PATH.unlink(missing_ok=True)
        print("daemon uninstalled")
        return 0
    if args.action == "stop":
        if not PLIST_PATH.exists():
            raise BridgeError("daemon is not installed")
        _launchctl("bootout", domain, str(PLIST_PATH), check=False)
        print("daemon stopped")
        return 0
    if args.action == "start":
        if not PLIST_PATH.exists():
            raise BridgeError("daemon is not installed")
        _launchctl("bootstrap", domain, str(PLIST_PATH))
        print("daemon started")
        return 0
    if args.action == "restart":
        if not PLIST_PATH.exists():
            raise BridgeError("daemon is not installed")
        # Ask the running job to unwind cleanly. KeepAlive=true causes launchd to
        # start a replacement after the SIGTERM handler exits. This avoids the
        # force-kill-first semantics of `kickstart -k`, which can strand a
        # configured-command process group if the daemon dies mid-command. If the
        # job is already stopped, first try to kickstart a loaded service and then
        # bootstrap the plist if it is no longer registered with launchd.
        service = f"{domain}/{LABEL}"
        if _launchctl("kill", "SIGTERM", service, check=False) != 0:
            if _launchctl("kickstart", service, check=False) != 0:
                _launchctl("bootstrap", domain, str(PLIST_PATH))
        print("daemon restart requested")
        return 0
    raise BridgeError(f"unknown daemon action: {args.action}")


def cmd_configure_command(args: argparse.Namespace) -> int:
    cfg = load_config()
    registry = load_registry()
    repo_id, _entry = resolve_repo(registry, args.repo)
    commands = cfg.setdefault("commands", {}).setdefault(repo_id, {})
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.name):
        raise BridgeError("command name must be a safe symbolic token")
    if not args.argv:
        raise BridgeError("command argv is required")
    commands[args.name] = args.argv
    save_config(cfg)
    print(f"configured command {args.name!r} for {repo_id}")
    return 0


def cmd_configure_concurrency(args: argparse.Namespace) -> int:
    cfg = load_config()
    cfg["max_workers"] = args.workers
    cfg["max_pending_jobs"] = args.max_pending_jobs
    save_config(cfg)
    print(
        f"configured concurrency: {args.workers} workers; "
        f"{args.max_pending_jobs} max pending jobs"
    )
    return 0


def cmd_configure_push(args: argparse.Namespace) -> int:
    cfg = load_config()
    registry = load_registry()
    repo_id, _entry = resolve_repo(registry, args.repo)
    enabled = {str(x) for x in cfg.get("push_enabled_repos", [])}
    if args.action == "enable":
        enabled.add(repo_id)
    else:
        enabled.discard(repo_id)
    cfg["push_enabled_repos"] = sorted(enabled)
    save_config(cfg)
    print(f"push {'enabled' if args.action == 'enable' else 'disabled'} for {repo_id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=APP_NAME)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("setup")
    s.add_argument("--remote")
    s.set_defaults(func=cmd_setup)

    s = sub.add_parser("add-root")
    s.add_argument("path")
    s.set_defaults(func=cmd_add_root)

    s = sub.add_parser("scan")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("materialize")
    s.add_argument("repo")
    s.set_defaults(func=cmd_materialize)

    s = sub.add_parser("status")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("watch")
    s.add_argument("--once", action="store_true")
    s.add_argument("--interval", type=float)
    s.set_defaults(func=cmd_watch)

    s = sub.add_parser("daemon")
    s.add_argument("action", choices=["install", "uninstall", "start", "stop", "restart"])
    s.add_argument("--command")
    s.set_defaults(func=cmd_daemon)

    s = sub.add_parser("configure-command")
    s.add_argument("repo")
    s.add_argument("name")
    s.add_argument("argv", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_configure_command)

    s = sub.add_parser("configure-concurrency")
    s.add_argument("--workers", type=int, required=True)
    s.add_argument("--max-pending-jobs", type=int, required=True)
    s.set_defaults(func=cmd_configure_concurrency)

    s = sub.add_parser("configure-push")
    s.add_argument("repo")
    s.add_argument("action", choices=["enable", "disable"])
    s.set_defaults(func=cmd_configure_push)

    return p


def main(argv: list[str] | None = None) -> int:
    _ensure_private_dirs()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except BridgeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
