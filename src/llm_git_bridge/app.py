from __future__ import annotations

import argparse
import configparser
import hashlib
import hmac
import json
import math
import os
import plistlib
import re
import secrets
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

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
from .transport import RcloneRCProcess, RcloneTransport, start_rclone_rcd, TransientTransportError
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


def _process_diagnostics_request(obj: dict[str, Any], filename: str) -> dict[str, Any]:
    txid = _validate_request_identity(obj, filename)
    limit = obj.get("limit", 10)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise BridgeError("diagnostics limit must be an integer from 1 to 50")
    return {
        "protocol": PROTOCOL_VERSION,
        "kind": "result",
        "operation": "diagnostics",
        "transaction_id": txid,
        "status": "success",
        "metrics": _recent_metrics(limit),
        "processed_at": utc_now(),
    }


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


def _prune_command_logs(limit: int = COMMAND_LOG_RETENTION) -> None:
    """Retain a small bounded set of local command logs for diagnosis."""
    if limit < 1:
        return
    root = STATE_DIR / "command-logs"
    if not root.exists():
        return
    try:
        candidates = [path for path in root.iterdir() if path.is_dir()]
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


def process_pending_once(cfg: dict[str, Any], *, cancel_check: Callable[[], bool] | None = None) -> int:
    transport = transport_from_config(cfg)
    poll_started = time.monotonic()
    list_started = time.monotonic()
    tx_entries = [
        entry
        for entry in transport.list_entries(f"{REMOTE_ROOT}/transactions")
        if entry.name.endswith(".json") and TX_FILENAME_RE.fullmatch(entry.name)
    ]
    tx_files = [entry.name for entry in tx_entries]
    tx_sizes = {entry.name: entry.size for entry in tx_entries}
    transaction_list_s = round(time.monotonic() - list_started, 4)
    transaction_list_transport = str(getattr(transport, "last_mode", "unknown"))

    # Normal polling lists only transactions. Replay recovery against the remote
    # result directory is performed once at watcher startup instead of here. A
    # malformed marker is not evidence of acknowledgement; recover it from an
    # authenticated remote result if one exists, otherwise process the request.
    candidates: list[str] = []
    published: list[str] = []
    invalid_marker_names: list[str] = []
    for name in tx_files:
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
        for name in list(invalid_marker_names):
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

    if not candidates:
        # The transaction directory is an inbox, not an audit log. Reap one old
        # acknowledged request per idle poll so listing cost remains bounded even
        # after long-lived use. RC-only cleanup avoids turning maintenance into a
        # subprocess latency penalty; failed cleanup is simply retried later.
        if published:
            acknowledged = published[0]
            _cleanup_remote_request(transport, acknowledged, allow_fallback=False)
            # A crash can occur after the authenticated publication marker is
            # durable but before request/local cleanup. Once a marker is valid,
            # local inbox/workspace/outbox staging is no longer replay authority;
            # retire it on the next idle poll as well as on the original path.
            _cleanup_local_request_artifacts(acknowledged)
            _prune_local_results()
            _prune_command_logs()
        return 0

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

    if not still_pending:
        return processed

    if not REGISTRY_FILE.exists():
        registry = refresh_registry(cfg, publish=True)
    else:
        registry = load_registry()

    for filename in still_pending:
        request_started = time.monotonic()
        tx_local = STATE_DIR / "inbox" / filename
        local_result = STATE_DIR / "results" / filename
        result: dict[str, Any]
        request_bytes_sha256: str | None = None
        transaction_download_s = 0.0
        transaction_download_transport = "none"
        try:
            reported_size = tx_sizes.get(filename)
            if reported_size is not None and reported_size > MAX_REQUEST_BYTES:
                raise BridgeError("remote request exceeds maximum allowed size")

            download_started = time.monotonic()
            try:
                raw = transport.download_text(
                    f"{REMOTE_ROOT}/transactions/{filename}",
                    tx_local,
                    max_bytes=MAX_REQUEST_BYTES,
                )
            except TransientTransportError:
                transaction_download_s = round(time.monotonic() - download_started, 4)
                transaction_download_transport = str(getattr(transport, "last_mode", "unknown"))
                tx_local.unlink(missing_ok=True)
                _append_metric({
                    "event": "transaction-download-retry",
                    "transaction_id": filename[:-5],
                    "transaction_list_s": transaction_list_s,
                    "transaction_download_s": transaction_download_s,
                    "transaction_list_transport": transaction_list_transport,
                    "transaction_download_transport": transaction_download_transport,
                    "poll_total_s": round(time.monotonic() - poll_started, 4),
                    "recorded_at": utc_now(),
                })
                # No request bytes were available to validate. Publishing a signed
                # terminal result here would turn a transient Drive/rclone failure
                # into durable application state. Leave the inbox object untouched
                # so a later poll can retry it safely.
                continue
            transaction_download_s = round(time.monotonic() - download_started, 4)
            transaction_download_transport = str(getattr(transport, "last_mode", "unknown"))
            request_bytes_sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            obj = strict_json_loads(raw.lstrip("\ufeff"))
            if not isinstance(obj, dict):
                raise BridgeError("request JSON must be an object")
            _validate_request_identity(obj, filename)
            kind = obj.get("kind")

            # The local registry is written atomically but may be refreshed by a
            # concurrent local `scan` while a poll is processing several queued
            # requests.  Re-read it immediately before repo-dependent dispatch so
            # later requests never act on a stale in-memory path/identity snapshot.
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
                repo_id, entry = resolve_repo(registry, obj.get("repo", ""))
                commands = cfg.get("commands", {}).get(repo_id, {})
                outcome = process_transaction(
                    Path(entry["path"]),
                    repo_id,
                    obj,
                    state_dir=STATE_DIR,
                    safe_branch_prefix=str(cfg.get("safe_branch_prefix", "ai/")),
                    commands=commands,
                    allow_commit=bool(cfg.get("allow_commit", True)),
                    allow_push=repo_id in {str(x) for x in cfg.get("push_enabled_repos", [])},
                    cancel_check=cancel_check,
                )
                result = outcome.result
                if outcome.snapshot is not None and obj.get("publish_snapshot", False):
                    branch = result["branch"]
                    snap_rel = f"{REMOTE_ROOT}/repos/{repo_id}/branches/{branch_token(branch)}/snapshot.json"
                    try:
                        transport.upload_json(
                            snap_rel,
                            outcome.snapshot,
                            STATE_DIR / "outbox" / f"snapshot-{repo_id}-{result['transaction_id']}.json",
                        )
                        result["snapshot"] = snap_rel
                    except Exception:
                        # The Git commit already succeeded. Preserve that success and
                        # report only the secondary snapshot-publication failure.
                        result["snapshot_error"] = "snapshot publication failed"
            else:
                raise BridgeError(f"unsupported request kind: {kind!r}")
        except Exception as exc:
            txid = filename[:-5]
            result = {
                "protocol": PROTOCOL_VERSION,
                "kind": "result",
                "transaction_id": txid,
                "status": "error",
                "processed_at": utc_now(),
                "error": _public_error_message(exc),
            }

        if request_bytes_sha256 is not None:
            # Bind the signed acknowledgement to the exact downloaded request bytes.
            # This lets clients distinguish an idempotent retry from transaction-ID reuse.
            result["request_bytes_sha256"] = request_bytes_sha256

        # Include timings knowable before acknowledgement in the durable result.
        # The result-upload duration itself is recorded locally after upload completes.
        result["transport_timings"] = {
            "transaction_list_s": transaction_list_s,
            "transaction_download_s": transaction_download_s,
            "transaction_list_transport": transaction_list_transport,
            "transaction_download_transport": transaction_download_transport,
            "pre_result_upload_s": round(time.monotonic() - request_started, 4),
        }

        # Authenticate the acknowledgement before either local or remote storage.
        # Mailbox writers can create files in results/, so startup replay recovery
        # never trusts a result filename or unsigned object on its own.
        result = _sign_result(filename, result)

        # Durable local result first; remote acknowledgement second. If upload fails,
        # the next poll republishes this result rather than re-executing the request.
        save_json(local_result, result)
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
        _prune_command_logs()
        _append_metric({
            "event": "transaction",
            "transaction_id": filename[:-5],
            "transaction_list_s": transaction_list_s,
            "transaction_download_s": transaction_download_s,
            "pre_result_upload_s": result["transport_timings"]["pre_result_upload_s"],
            "result_upload_s": result_upload_s,
            "transaction_list_transport": transaction_list_transport,
            "transaction_download_transport": transaction_download_transport,
            "result_upload_transport": result_upload_transport,
            "request_cleanup_status": cleanup_status,
            "request_cleanup_s": cleanup_s,
            "request_cleanup_transport": cleanup_transport,
            "request_total_s": round(time.monotonic() - request_started, 4),
            "poll_total_s": round(time.monotonic() - poll_started, 4),
            "recorded_at": utc_now(),
        })
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
    try:
        cfg = load_config()
        rc_handle = _start_watch_rcd(cfg)
        if args.once:
            reconcile_remote_results(cfg)
            reconcile_local_acknowledged_artifacts()
            count = process_pending_once(cfg)
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
                count = process_pending_once(current_cfg, cancel_check=lambda: stop)
                if count:
                    print(f"processed: {count}", flush=True)
            except Exception as exc:
                print(f"watch error: {_public_error_message(exc)}", file=sys.stderr, flush=True)
            deadline = time.monotonic() + interval
            while not stop and time.monotonic() < deadline:
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        return 0
    finally:
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
