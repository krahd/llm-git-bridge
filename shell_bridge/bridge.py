#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import secrets
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

PROTOCOL = 1
VERSION = "6"
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
DEFAULT_OPERATOR_CONFIRMATION_TIMEOUT = 300
DEFAULT_WAKE_GRACE_SECONDS = 3600.0
PRODUCT_NAME = "Local Executor Bridge"
SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
DEFAULT_CAFFEINATE = Path("/usr/bin/caffeinate")
WRITE_SCOPES = {"auto", "read_only", "repository", "system"}
WORKSPACE_COORDINATOR = Path(__file__).resolve().with_name("workspace.py")
ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")

HIGH_IMPACT_COMMAND_RULES = (
    (
        "github_repository_control_plane",
        re.compile(r"(?im)(?<![\w-])gh\s+repo\s+(?:create|delete|rename|archive|unarchive|edit)\b"),
    ),
    (
        "github_api_mutation",
        re.compile(r"(?im)(?<![\w-])gh\s+api\b[^\n;]*(?:(?:-X|--method(?:=|\s+))\s*(?:POST|PUT|PATCH|DELETE))\b"),
    ),
    (
        "github_content_control_plane",
        re.compile(
            r"(?im)(?<![\w-])gh\s+(?:"
            r"issue\s+(?:create|close|delete|edit|reopen|transfer|comment|lock|unlock|pin|unpin)|"
            r"pr\s+(?:create|close|edit|merge|ready|reopen|review|comment|lock|unlock)|"
            r"release\s+(?:create|delete|edit|upload|delete-asset)|"
            r"workflow\s+(?:run|enable|disable)|"
            r"secret\s+(?:set|delete)|variable\s+(?:set|delete)|"
            r"gist\s+(?:create|delete|edit)"
            r")\b"
        ),
    ),
    (
        "http_mutation",
        re.compile(
            r"(?im)(?<![\w-])(?:"
            r"curl\b[^\n;]*(?:(?:-X\s*(?:POST|PUT|PATCH|DELETE)\b)|(?:--request(?:=|\s+)(?:POST|PUT|PATCH|DELETE)\b)|(?:\s-(?:d|F|T)(?:\s|[^A-Za-z]))|(?:--(?:data(?:-[\w-]+)?|form(?:-string)?|upload-file)(?:=|\s+)))|"
            r"wget\b[^\n;]*(?:(?:--method(?:=|\s+)(?:POST|PUT|PATCH|DELETE)\b)|(?:--post-(?:data|file)(?:=|\s+)))"
            r")"
        ),
    ),
    (
        "remote_shell_or_copy",
        re.compile(r"(?im)(?:^|[;&|]\s*|\n)\s*(?:ssh|scp|sftp|rsync)\b"),
    ),
    (
        "git_destructive_push",
        re.compile(r"(?im)(?<![\w-])git\s+push\b[^\n;]*(?:--force(?:-with-lease)?\b|--delete\b|(?:^|\s)-f(?:\s|$))"),
    ),
    (
        "git_destructive_local",
        re.compile(r"(?im)(?<![\w-])git\s+(?:reset\s+--hard\b|clean\s+-[A-Za-z]*f[A-Za-z]*\b)"),
    ),
    (
        "filesystem_recursive_delete",
        re.compile(r"(?im)(?:^|[;&|]\s*|\n)\s*(?:sudo\s+)?rm\s+(?:-[A-Za-z]*r[A-Za-z]*f[A-Za-z]*|-[A-Za-z]*f[A-Za-z]*r[A-Za-z]*)\b"),
    ),
)

NON_REPOSITORY_WRITE_RULES = (
    (
        "filesystem_mutation",
        re.compile(r"(?im)(?:^|[;&|]\s*|\n)\s*(?:sudo\s+)?(?:cp|mv|rm|mkdir|rmdir|touch|chmod|chown|ln|install|tee)\b"),
    ),
    (
        "system_configuration",
        re.compile(r"(?im)(?<![\w-])(?:defaults\s+(?:write|delete|rename|import)|launchctl\s+(?:bootstrap|bootout|kickstart|enable|disable|setenv|unsetenv))\b"),
    ),
    (
        "package_management",
        re.compile(r"(?im)(?<![\w-])(?:brew|pip3?|npm|pnpm|yarn)\s+(?:install|uninstall|upgrade|update|link|unlink|add|remove)\b"),
    ),
)

_TRANSPORT_LOCK = threading.RLock()
_ACTIVE_LOCK = threading.RLock()
_ACTIVE: dict[str, dict] = {}
_SHUTDOWN = threading.Event()
_WAKE_LEASE = None


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
    explanation = req.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        raise ValueError("explanation is required and must be a non-empty string")
    explanation = explanation.strip()
    if len(explanation.encode("utf-8")) > 4096:
        raise ValueError("explanation is too large")
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
    write_scope = req.get("write_scope", "auto")
    if write_scope not in WRITE_SCOPES:
        raise ValueError("write_scope must be auto, read_only, repository, or system")
    return {
        "id": rid, "cwd": cwd, "command": command, "timeout": timeout, "stdin": stdin,
        "write_scope": write_scope, "explanation": explanation,
    }


def high_impact_command_category(command: str) -> str | None:
    # Network/control-plane effects cannot be made read-only by a filesystem sandbox.
    # These rules are therefore an additional approval gate, not the primary sandbox.
    for category, pattern in HIGH_IMPACT_COMMAND_RULES:
        if pattern.search(command):
            return category
    return None


def non_repository_write_category(command: str) -> str | None:
    for category, pattern in NON_REPOSITORY_WRITE_RULES:
        if pattern.search(command):
            return category
    return None


def _path_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _git_repository_write_roots(cwd: Path, allowed_root: Path) -> list[Path] | None:
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        cp = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel", "--git-dir", "--git-common-dir"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=3, check=False, env=env,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if cp.returncode != 0:
        return None
    lines = [line.strip() for line in cp.stdout.splitlines() if line.strip()]
    if len(lines) != 3:
        return None
    roots: list[Path] = []
    for index, raw in enumerate(lines):
        value = Path(raw).expanduser()
        if not value.is_absolute():
            value = (cwd / value).resolve()
        else:
            value = value.resolve()
        if not _path_under(value, allowed_root):
            return None
        if value not in roots:
            roots.append(value)
    return roots


def _trusted_workspace_coordinator(command: str, allowed_root: Path, state_dir: Path) -> bool:
    """Recognise exactly one workspace.py invocation with no outer-shell control operators.

    The coordinator itself is privileged because it must fetch/push and manage worktrees.
    Remote shell text carried by `workspace exec` is sandboxed *inside* workspace.py.
    """
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>`$()")
        lexer.whitespace_split = True
        tokens = list(lexer)
        if any(token and all(ch in ";&|<>`$()" for ch in token) for token in tokens):
            return False
        argv = shlex.split(command, posix=True)
    except ValueError:
        return False
    if len(argv) < 3 or not Path(argv[0]).name.startswith("python"):
        return False
    try:
        script = Path(argv[1]).expanduser().resolve()
    except OSError:
        return False
    if script != WORKSPACE_COORDINATOR.expanduser().resolve():
        return False
    subcommand = argv[2]
    allowed_subcommands = {"create", "ensure", "exec", "ready", "integrate", "show", "list", "recover", "gc", "mark-reconciled"}
    if subcommand not in allowed_subcommands:
        return False

    def option(name: str) -> str | None:
        try:
            i = argv.index(name)
        except ValueError:
            return None
        return argv[i + 1] if i + 1 < len(argv) else None

    root = allowed_root.expanduser().resolve()
    if subcommand in {"create", "ensure", "recover"}:
        raw_repo = option("--repo")
        if not raw_repo:
            return False
        try:
            Path(raw_repo).expanduser().resolve().relative_to(root)
        except (ValueError, OSError):
            return False
    elif subcommand == "gc":
        raw_repo = option("--repo")
        if not raw_repo:
            return False
        try:
            Path(raw_repo).expanduser().resolve().relative_to(root)
        except (ValueError, OSError):
            return False
    elif subcommand in {"exec", "ready", "integrate", "show", "mark-reconciled"}:
        job_id = option("--job")
        if not job_id or not ID_RE.fullmatch(job_id):
            return False
        job_file = state_dir.expanduser().resolve() / "workspaces" / "jobs" / f"{job_id}.json"
        try:
            job = json.loads(job_file.read_text("utf-8"))
            Path(job["repo"]).expanduser().resolve().relative_to(root)
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return False
    return True

def resolve_write_plan(request: dict, cfg: dict) -> dict:
    allowed_root = Path(cfg["allowed_root"]).expanduser().resolve()
    state_dir = Path(cfg["state_dir"]).expanduser().resolve()
    requested = request.get("write_scope", "auto")
    if requested not in WRITE_SCOPES:
        raise ValueError("write_scope must be auto, read_only, repository, or system")

    if requested == "system":
        return {
            "requested": requested, "effective": "system", "write_roots": None,
            "read_roots": None, "deny_home_reads": False, "allow_network": True,
            "confirmation_category": "system_write",
        }
    if requested == "read_only":
        return {
            "requested": requested, "effective": "read_only", "write_roots": [],
            "read_roots": [allowed_root], "deny_home_reads": True, "allow_network": False,
        }

    if _trusted_workspace_coordinator(request["command"], allowed_root, state_dir):
        return {
            "requested": requested, "effective": "repository", "write_roots": None,
            "read_roots": None, "deny_home_reads": False, "allow_network": True,
            "trusted_coordinator": True,
        }

    repo_roots = _git_repository_write_roots(request["cwd"], allowed_root)
    if requested == "repository":
        if repo_roots is None:
            raise ValueError("write_scope=repository requires cwd inside a Git repository/worktree or an exact trusted workspace coordinator invocation")
        return {
            "requested": requested, "effective": "repository", "write_roots": repo_roots,
            "read_roots": [allowed_root], "deny_home_reads": True, "allow_network": False,
        }

    if repo_roots is not None:
        return {
            "requested": requested, "effective": "repository", "write_roots": repo_roots,
            "read_roots": [allowed_root], "deny_home_reads": True, "allow_network": False,
        }

    write_hint = non_repository_write_category(request["command"])
    if write_hint is not None:
        return {
            "requested": requested, "effective": "system", "write_roots": None,
            "read_roots": None, "deny_home_reads": False, "allow_network": True,
            "confirmation_category": f"non_repository_{write_hint}",
        }
    return {
        "requested": requested, "effective": "read_only", "write_roots": [],
        "read_roots": [allowed_root], "deny_home_reads": True, "allow_network": False,
    }

def public_write_plan(plan: dict) -> dict:
    return {k: v for k, v in plan.items() if k not in {"write_roots", "read_roots"}}


def _sbpl_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def sandbox_profile(write_roots: list[Path], *, allow_network: bool = True,
                    read_roots: list[Path] | None = None,
                    deny_home_reads: bool = False) -> str:
    roots: list[Path] = []
    for root in write_roots:
        try:
            resolved = root.expanduser().resolve()
        except OSError:
            continue
        if resolved not in roots:
            roots.append(resolved)
    write_clauses = " ".join(f'(subpath "{_sbpl_string(str(root))}")' for root in roots)
    parts = ["(version 1)", "(allow default)", "(deny file-write*)"]
    if not allow_network:
        parts.append("(deny network*)")
    if deny_home_reads:
        home = Path.home().resolve()
        parts.append(f'(deny file-read* (subpath "{_sbpl_string(str(home))}"))')
        allowed_reads: list[Path] = []
        for root in read_roots or []:
            try:
                resolved = root.expanduser().resolve()
            except OSError:
                continue
            if resolved not in allowed_reads:
                allowed_reads.append(resolved)
        read_clauses = " ".join(f'(subpath "{_sbpl_string(str(root))}")' for root in allowed_reads)
        if read_clauses:
            parts.append(f"(allow file-read* {read_clauses})")
    parts.append(f'(allow file-write* (literal "/dev/null") {write_clauses})')
    return "".join(parts)

def operator_confirmation_mode(cfg: dict) -> str:
    configured = cfg.get("operator_confirmation_mode", "auto")
    if configured not in {"auto", "dialog", "reject", "off"}:
        raise ValueError("operator_confirmation_mode must be auto, dialog, reject, or off")
    if configured == "auto":
        return "dialog" if sys.platform == "darwin" else "reject"
    return configured


def _confirmation_command_excerpt(command: str, limit: int = 6000) -> str:
    if len(command) <= limit:
        return command
    return command[:limit] + "\n… [command truncated in dialogue; full request remains journalled]"





def _secure_approval_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise ValueError(f"unsafe approval directory: {path}")
    if st.st_uid != os.getuid():
        raise ValueError(f"approval directory has unexpected owner: {path}")
    path.chmod(0o700)


def _approval_record_path(root: Path, bucket: str, request_id: str) -> Path:
    validate_request_name(f"{request_id}.json")
    bucket_path = root / bucket
    _secure_approval_dir(root)
    _secure_approval_dir(bucket_path)
    target = bucket_path / f"{request_id}.json"
    try:
        st = target.lstat()
    except FileNotFoundError:
        return target
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
        raise ValueError(f"unsafe approval record: {target}")
    return target


def _approval_projection(*, request_id: str, bridge_instance_id: str, nonce: str, cwd: Path,
                         command: str, category: str, explanation: str, requested_write_scope: str,
                         effective_write_scope: dict) -> dict:
    network_authority = bool(effective_write_scope.get("allow_network", False))
    authority_summary = {
        "requested_write_scope": requested_write_scope,
        "effective_write_scope": effective_write_scope,
        "network_authority": network_authority,
    }
    effect_summary = {"category": category, "cwd": str(cwd)}
    return {
        "schema": 1,
        "request_id": request_id,
        "bridge_instance_id": bridge_instance_id,
        "nonce": nonce,
        "category": category,
        "explanation": explanation,
        "cwd": str(cwd),
        "command": command,
        "requested_write_scope": requested_write_scope,
        "effective_write_scope": effective_write_scope,
        "network_authority": network_authority,
        "authority_summary": authority_summary,
        "effect_summary": effect_summary,
    }


def _approval_payload_hash(projection: dict) -> str:
    payload = json.dumps(projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _atomic_json(path: Path, obj: dict, *, replace: bool = True) -> None:
    _secure_approval_dir(path.parent)
    if path.exists() or path.is_symlink():
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            raise ValueError(f"unsafe approval record: {path}")
        if not replace:
            raise FileExistsError(path)
    tmp = path.with_name(path.name + ".tmp-" + secrets.token_hex(6))
    data = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if not replace and (path.exists() or path.is_symlink()):
            raise FileExistsError(path)
        os.replace(tmp, path)
        try:
            dirfd = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(dirfd)
            finally: os.close(dirfd)
        except OSError:
            pass
    finally:
        try: tmp.unlink()
        except FileNotFoundError: pass


def _read_json_file(path: Path) -> dict | None:
    try:
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            return None
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            raw = os.read(fd, max(1, st.st_size + 1)).decode("utf-8")
        finally:
            os.close(fd)
        obj = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def _approval_signature_message(pending: dict) -> bytes:
    values = [
        "local-executor-approval-v1",
        str(pending.get("bridge_instance_id", "")),
        str(pending.get("request_id", "")),
        str(pending.get("nonce", "")),
        str(pending.get("payload_sha256", "")),
        str(pending.get("expires_at", "")),
    ]
    return ("\n".join(values) + "\n").encode("utf-8")


def _verify_approval_signature(decision: dict, pending: dict, cfg: dict) -> tuple[bool, str]:
    if decision.get("client") != "mac_gui":
        return False, "operator approval client is invalid; request was not started"
    if decision.get("decision") != "allow":
        return True, ""
    if decision.get("signature_algorithm") != "ecdsa-p256-sha256":
        return False, "operator approval signature algorithm is invalid; request was not started"
    public_key_path = cfg.get("operator_approval_public_key")
    pinned = cfg.get("operator_approval_public_key_sha256")
    if not isinstance(public_key_path, str) or not public_key_path or not isinstance(pinned, str) or not re.fullmatch(r"[0-9a-f]{64}", pinned):
        return False, "operator approval public key is not pinned; request was not started"
    key_path = Path(public_key_path).expanduser()
    try:
        st = key_path.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            return False, "operator approval public key path is unsafe; request was not started"
        key_bytes = key_path.read_bytes()
    except OSError as exc:
        return False, f"operator approval public key unavailable: {exc}"
    actual = hashlib.sha256(key_bytes).hexdigest()
    if not secrets.compare_digest(actual, pinned) or decision.get("signer_key_id") != pinned:
        return False, "operator approval signer does not match pinned public key; request was not started"
    signature_b64 = decision.get("signature_b64")
    if not isinstance(signature_b64, str) or not signature_b64:
        return False, "operator approval signature is missing; request was not started"
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except Exception:
        return False, "operator approval signature encoding is invalid; request was not started"
    with tempfile.TemporaryDirectory(prefix="leb-approval-verify-") as td:
        msg = Path(td) / "message"
        sig = Path(td) / "signature"
        msg.write_bytes(_approval_signature_message(pending))
        sig.write_bytes(signature)
        cp = subprocess.run(
            ["/usr/bin/openssl", "dgst", "-sha256", "-verify", str(key_path), "-signature", str(sig), str(msg)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False,
        )
    if cp.returncode != 0:
        return False, "operator approval signature verification failed; request was not started"
    return True, ""


def _launch_operator_approval_helper(app_path: Path) -> tuple[bool, str]:
    if not app_path.is_dir():
        return False, f"operator approval helper is not installed: {app_path}"
    try:
        cp = subprocess.run(["/usr/bin/open", str(app_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"operator approval helper could not be launched: {exc}"
    if cp.returncode != 0:
        detail = cp.stderr.strip() or cp.stdout.strip() or f"open exited {cp.returncode}"
        return False, f"operator approval helper could not be launched: {detail}"
    return True, ""


def _archive_approval_record(path: Path, root: Path, bucket: str, request_id: str) -> None:
    if not path.exists():
        return
    archive = root / bucket
    _secure_approval_dir(archive)
    target = archive / f"{request_id}-{int(time.time())}-{secrets.token_hex(4)}.json"
    os.replace(path, target)


def request_operator_confirmation(*, request_id: str, cwd: Path, command: str, category: str,
                                  cfg: dict, explanation: str, requested_write_scope: str,
                                  effective_write_scope: dict) -> dict:
    mode = operator_confirmation_mode(cfg)
    record = {"required": True, "category": category, "mode": mode, "approved": False, "state": "invalid"}
    if mode == "off":
        return {**record, "approved": True, "state": "approved", "message": "operator confirmation explicitly disabled by local configuration"}
    if mode == "reject":
        return {**record, "state": "rejected", "message": "operator confirmation required; interactive approval is unavailable or disabled"}
    timeout = cfg.get("operator_confirmation_timeout_seconds", DEFAULT_OPERATOR_CONFIRMATION_TIMEOUT)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
        return {**record, "message": "operator confirmation timeout configuration is invalid"}
    state_dir = Path(cfg.get("state_dir", "")).expanduser()
    if not str(state_dir):
        return {**record, "message": "operator confirmation state directory is unavailable"}
    instance = cfg.get("bridge_instance_id")
    if not isinstance(instance, str) or not instance:
        return {**record, "message": "bridge instance identity is unavailable"}
    app_path = Path(cfg.get("operator_approval_app", Path(__file__).resolve().parent / "Local Executor Approval.app"))
    approval_root = state_dir / "approvals"
    try:
        pending_path = _approval_record_path(approval_root, "pending", request_id)
        decision_path = _approval_record_path(approval_root, "decisions", request_id)
    except ValueError as exc:
        return {**record, "message": str(exc)}
    now = time.time()
    pending = _read_json_file(pending_path)
    created = False
    if pending is None:
        if pending_path.exists() or pending_path.is_symlink():
            return {**record, "message": "operator approval pending record is unsafe or unreadable; request was not started"}
        nonce = secrets.token_hex(32)
        projection = _approval_projection(
            request_id=request_id, bridge_instance_id=instance, nonce=nonce, cwd=cwd, command=command,
            category=category, explanation=explanation, requested_write_scope=requested_write_scope,
            effective_write_scope=effective_write_scope,
        )
        payload_hash = _approval_payload_hash(projection)
        expires_at = now + timeout
        pending = {
            "protocol": 1, "schema": 1, "kind": "operator_approval_request", **projection,
            "payload_sha256": payload_hash, "created_at": now, "expires_at": expires_at,
        }
        try:
            _atomic_json(pending_path, pending, replace=False)
        except Exception as exc:
            return {**record, "message": f"operator approval challenge could not be persisted safely: {exc}"}
        created = True
    else:
        if pending.get("protocol") != 1 or pending.get("schema") != 1 or pending.get("kind") != "operator_approval_request":
            return {**record, "message": "operator approval challenge schema is invalid; request was not started"}
        projection = _approval_projection(
            request_id=request_id, bridge_instance_id=instance, nonce=str(pending.get("nonce", "")), cwd=cwd, command=command,
            category=category, explanation=explanation, requested_write_scope=requested_write_scope,
            effective_write_scope=effective_write_scope,
        )
        expected = _approval_payload_hash(projection)
        if pending.get("payload_sha256") != expected or any(pending.get(k) != v for k, v in projection.items()):
            return {**record, "message": "operator approval state does not match this exact request; request was not started"}
    expires_at = pending.get("expires_at")
    if not isinstance(expires_at, (int, float)) or expires_at <= now:
        try: _archive_approval_record(pending_path, approval_root, "expired", request_id)
        except Exception: pass
        return {**record, "state": "expired", "message": "operator confirmation expired; request was not started"}
    decision = _read_json_file(decision_path)
    if decision is None:
        if decision_path.exists() or decision_path.is_symlink():
            return {**record, "message": "operator approval decision record is unsafe or unreadable; request was not started"}
        if created:
            launched, message = _launch_operator_approval_helper(app_path)
            if not launched:
                return {**record, "message": message}
        return {**record, "state": "pending", "message": "operator approval is pending", "expires_at": expires_at, "payload_sha256": pending["payload_sha256"]}
    if (decision.get("protocol") != 1 or decision.get("schema") != 1 or decision.get("kind") != "operator_approval_decision" or
        decision.get("request_id") != request_id or decision.get("bridge_instance_id") != instance or
        decision.get("nonce") != pending.get("nonce") or decision.get("payload_sha256") != pending.get("payload_sha256") or
        decision.get("client") != "mac_gui" or not isinstance(decision.get("step_up"), dict)):
        return {**record, "message": "operator approval decision failed exact-request binding; request was not started"}
    decided_at = decision.get("decided_at")
    if not isinstance(decided_at, (int, float)) or decided_at > expires_at or time.time() > expires_at:
        return {**record, "state": "expired", "message": "operator approval decision expired; request was not started"}
    value = decision.get("decision")
    if value == "allow":
        ok, message = _verify_approval_signature(decision, pending, cfg)
        if not ok:
            return {**record, "message": message}
        return {**record, "approved": True, "state": "approved", "message": "operator approved exact elevated request",
                "expires_at": expires_at, "payload_sha256": pending["payload_sha256"], "pending_path": str(pending_path), "decision_path": str(decision_path)}
    if value == "cancel":
        return {**record, "state": "rejected", "message": "operator declined elevated request; request was not started",
                "pending_path": str(pending_path), "decision_path": str(decision_path)}
    return {**record, "message": "operator approval decision is invalid; request was not started"}


def _pending_approval_ids(cfg: dict) -> list[str]:
    root = Path(cfg["state_dir"]).expanduser() / "approvals" / "pending"
    if not root.is_dir() or root.is_symlink():
        return []
    now = time.time()
    out = []
    for path in root.glob("*.json"):
        obj = _read_json_file(path)
        if obj and obj.get("kind") == "operator_approval_request" and isinstance(obj.get("expires_at"), (int, float)) and obj["expires_at"] > now:
            out.append(str(obj.get("request_id", path.stem)))
    return sorted(set(out))

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


def child_environment(request_id: str | None = None, *, sandboxed: bool = False) -> dict:
    env = os.environ.copy()
    if request_id:
        env["CHATGPT_SHELL_BRIDGE_REQUEST_ID"] = request_id
    if sandboxed:
        env["GIT_OPTIONAL_LOCKS"] = "0"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
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
              on_spawn=None,
              sandbox_write_roots: list[Path] | None = None,
              sandbox_allow_network: bool = True,
              sandbox_read_roots: list[Path] | None = None,
              sandbox_deny_home_reads: bool = False) -> dict:
    shell_path = Path(shell)
    if not shell_path.is_absolute() or not shell_path.is_file() or not os.access(shell_path, os.X_OK):
        raise ValueError("configured shell must be an existing absolute file")
    if max_output_bytes < 1:
        raise ValueError("max_output_bytes must be positive")

    sandboxed = sandbox_write_roots is not None
    sandbox_temp: Path | None = None
    argv = [str(shell_path), "-lc", command]
    env = child_environment(request_id, sandboxed=sandboxed)
    if sandboxed:
        if sys.platform != "darwin" or not SANDBOX_EXEC.is_file() or not os.access(SANDBOX_EXEC, os.X_OK):
            raise ValueError("execution sandbox is unavailable; refusing non-system shell execution")
        sandbox_temp = Path(tempfile.mkdtemp(prefix=f"chatgpt-shell-{request_id or 'request'}-"))
        profile_roots = [sandbox_temp, *sandbox_write_roots]
        argv = [str(SANDBOX_EXEC), "-p", sandbox_profile(
            profile_roots,
            allow_network=sandbox_allow_network,
            read_roots=[sandbox_temp, *(sandbox_read_roots or [])],
            deny_home_reads=sandbox_deny_home_reads,
        ), *argv]
        env["TMPDIR"] = str(sandbox_temp)
        env["TMP"] = str(sandbox_temp)
        env["TEMP"] = str(sandbox_temp)
        env["TMPPREFIX"] = str(sandbox_temp / "zsh")

    started = time.time()
    try:
        p = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env=env,
        )
    except Exception:
        if sandbox_temp is not None:
            shutil.rmtree(sandbox_temp, ignore_errors=True)
        raise
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
    if sandbox_temp is not None:
        shutil.rmtree(sandbox_temp, ignore_errors=True)
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
        "filesystem_sandboxed": sandboxed,
        "network_sandboxed": sandboxed and not sandbox_allow_network,
        "sandbox_write_roots": None if sandbox_write_roots is None else [str(Path(root).resolve()) for root in sandbox_write_roots],
        "sandbox_read_roots": None if sandbox_read_roots is None else [str(Path(root).resolve()) for root in sandbox_read_roots],
        "home_read_restricted": bool(sandboxed and sandbox_deny_home_reads),
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

    try:
        write_plan = resolve_write_plan(v, cfg)
    except Exception as exc:
        result = result_envelope(v["id"], request_sha, "rejected", {"cwd": str(v["cwd"]), "message": str(exc)})
        _persist_then_publish(result, local_result, finished_marker, name, cfg)
        return

    confirmation = None
    high_impact_category = high_impact_command_category(v["command"])
    category = high_impact_category or write_plan.get("confirmation_category")
    if category is not None:
        confirmation = request_operator_confirmation(
            request_id=v["id"], cwd=v["cwd"], command=v["command"], category=category, cfg=cfg,
            explanation=v.get("explanation"), requested_write_scope=v.get("write_scope", "auto"),
            effective_write_scope=public_write_plan(write_plan),
        )
        if confirmation.get("state") == "pending":
            atomic_write(request_dir / "state.json", json.dumps({"state":"AWAITING_APPROVAL","request_sha256":request_sha,"updated_at":time.time()}, sort_keys=True).encode("utf-8"))
            return
        if not confirmation["approved"]:
            result = result_envelope(v["id"], request_sha, "rejected", {
                "cwd": str(v["cwd"]), "message": confirmation["message"],
                "write_scope": public_write_plan(write_plan), "operator_confirmation": confirmation,
            })
            _persist_then_publish(result, local_result, finished_marker, name, cfg)
            return
        # Re-check expiry immediately before STARTED and consume the one-time decision.
        if time.time() >= float(confirmation.get("expires_at", 0)):
            result = result_envelope(v["id"], request_sha, "rejected", {"cwd":str(v["cwd"]),"message":"operator approval expired before execution","operator_confirmation":confirmation})
            _persist_then_publish(result, local_result, finished_marker, name, cfg); return
        approval_marker = request_dir / "approval.json"
        _atomic_json(approval_marker, {"request_sha256":request_sha,"payload_sha256":confirmation.get("payload_sha256"),"expires_at":confirmation.get("expires_at"),"approved_at":time.time()})
        approval_root = Path(cfg["state_dir"]).expanduser() / "approvals"
        try:
            _archive_approval_record(Path(confirmation["decision_path"]), approval_root, "consumed", v["id"])
            _archive_approval_record(Path(confirmation["pending_path"]), approval_root, "audit", v["id"])
        except Exception as exc:
            result = result_envelope(v["id"], request_sha, "rejected", {"cwd":str(v["cwd"]),"message":f"could not consume approval atomically: {exc}","operator_confirmation":confirmation})
            _persist_then_publish(result, local_result, finished_marker, name, cfg); return
        if high_impact_category is not None:
            write_plan["allow_network"] = True

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
            sandbox_write_roots=write_plan["write_roots"],
            sandbox_allow_network=write_plan.get("allow_network", True),
            sandbox_read_roots=write_plan.get("read_roots"),
            sandbox_deny_home_reads=bool(write_plan.get("deny_home_reads", False)),
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
                "write_scope": public_write_plan(write_plan),
                **({"operator_confirmation": confirmation} if confirmation is not None else {}),
                **exec_result,
            })
    except Exception as exc:
        result = result_envelope(v["id"], request_sha, "indeterminate", {
            "message": f"execution raised after STARTED: {type(exc).__name__}: {exc}",
            "write_scope": public_write_plan(write_plan),
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
        ("operator_confirmation_timeout_seconds", DEFAULT_OPERATOR_CONFIRMATION_TIMEOUT),
    ]:
        value = cfg.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{key} must be a positive integer")
    max_active_requests(cfg)
    operator_confirmation_mode(cfg)
    return cfg


def _instance_lock_path(cfg: dict) -> Path:
    """Return a per-user lock path stable across state-dir/config migrations."""
    instance_id = cfg.get("bridge_instance_id")
    if isinstance(instance_id, str) and instance_id.strip():
        identity = f"instance:{instance_id.strip()}"
    else:
        # Backward-compatible fallback for incomplete/legacy configs.
        remote = str(cfg.get("remote", ""))
        base = str(cfg.get("base_path", ""))
        identity = f"mailbox:{remote}|{base}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    lock_root = Path(tempfile.gettempdir()) / f"chatgpt-shell-bridge-{os.getuid()}"
    lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        lock_root.chmod(0o700)
    except OSError:
        pass
    return lock_root / f"{digest}.lock"


def acquire_instance_lock(cfg: dict):
    lock_file = _instance_lock_path(cfg).open("a+")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_file.close()
        raise RuntimeError("another shell-bridge instance already holds the instance lock") from exc
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(f"pid={os.getpid()}\n")
    lock_file.flush()
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


def _private_oauth_client_configured(cfg: dict) -> bool | None:
    """Return whether the rclone remote has its own client_id, without exposing it."""
    remote = str(cfg.get("remote", "")).rstrip(":")
    if not remote:
        return None
    safe_cfg = dict(cfg)
    # `rclone config redacted` is local config inspection; Drive backend flags do not apply.
    safe_cfg.pop("drive_root_folder_id", None)
    try:
        cp = run_rclone(["config", "redacted", remote], safe_cfg, check=False)
    except Exception:
        return None
    if cp.returncode != 0:
        return None
    text = (cp.stdout or b"").decode("utf-8", errors="replace")
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "client_id":
            return bool(value.strip())
    return False


class WakeLease:
    """Own one macOS idle-sleep assertion while executor work is active or in grace."""

    def __init__(self, cfg: dict):
        self.enabled = bool(cfg.get("wake_lease_enabled", True))
        self.grace_seconds = float(cfg.get("wake_grace_seconds", DEFAULT_WAKE_GRACE_SECONDS))
        self.caffeinate = Path(cfg.get("caffeinate_path", str(DEFAULT_CAFFEINATE))).expanduser()
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._state = "off"
        self._reason: str | None = None
        self._acquired_at: float | None = None
        self._idle_since: float | None = None
        self._release_at_mono: float | None = None

    def _ensure_process(self) -> None:
        if not self.enabled or self._proc is not None:
            return
        if sys.platform != "darwin" or not self.caffeinate.is_file() or not os.access(self.caffeinate, os.X_OK):
            return
        self._proc = subprocess.Popen(
            [str(self.caffeinate), "-i", "-w", str(os.getpid())],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._acquired_at = time.time()

    def acquire(self, reason: str) -> None:
        with self._lock:
            self._ensure_process()
            if self._proc is None:
                return
            self._state = "busy"
            self._reason = reason
            self._idle_since = None
            self._release_at_mono = None

    def enter_idle_grace(self) -> None:
        with self._lock:
            if self._proc is None or self._state == "grace":
                return
            self._state = "grace"
            self._reason = "idle_grace"
            self._idle_since = time.time()
            self._release_at_mono = time.monotonic() + max(0.0, self.grace_seconds)

    def tick(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is not None:
                self._proc = None
                self._state = "off"
                self._reason = None
                self._release_at_mono = None
            if self._proc is not None and self._release_at_mono is not None and time.monotonic() >= self._release_at_mono:
                self._release_locked()

    def _release_locked(self) -> None:
        proc = self._proc
        self._proc = None
        self._state = "off"
        self._reason = None
        self._idle_since = None
        self._release_at_mono = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def close(self) -> None:
        with self._lock:
            self._release_locked()

    def snapshot(self) -> dict:
        with self._lock:
            remaining = None
            if self._release_at_mono is not None:
                remaining = max(0.0, self._release_at_mono - time.monotonic())
            return {
                "enabled": self.enabled,
                "state": self._state,
                "reason": self._reason,
                "acquired_at": None if self._acquired_at is None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._acquired_at)),
                "idle_since": None if self._idle_since is None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._idle_since)),
                "grace_seconds": self.grace_seconds,
                "grace_remaining_seconds": None if remaining is None else round(remaining, 3),
                "mechanism": "caffeinate -i -w <daemon-pid>",
                "pid": None if self._proc is None else self._proc.pid,
            }


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
        "operator_confirmation_mode": operator_confirmation_mode(cfg),
        "operator_confirmation_timeout_seconds": cfg.get("operator_confirmation_timeout_seconds", DEFAULT_OPERATOR_CONFIRMATION_TIMEOUT),
        "product_name": PRODUCT_NAME,
        "default_write_scope": "auto",
        "raw_repository_network_default": "deny",
        "raw_home_read_default": "deny_except_allowed_root",
        "filesystem_sandbox_available": bool(sys.platform == "darwin" and SANDBOX_EXEC.is_file() and os.access(SANDBOX_EXEC, os.X_OK)),
        "filesystem_sandbox_path": str(SANDBOX_EXEC),
        "rclone_timeout_seconds": int(cfg.get("rclone_timeout_seconds", DEFAULT_RCLONE_TIMEOUT)),
        "state_dir": str(Path(cfg["state_dir"]).expanduser().resolve()),
        "state_counter_semantics": "current_state_dir_journal_snapshot",
        "health_interval_seconds": float(cfg.get("health_seconds", DEFAULT_HEALTH_SECONDS)),
        "health_stale_after_seconds": max(
            2.0 * float(cfg.get("health_seconds", DEFAULT_HEALTH_SECONDS)),
            float(cfg.get("health_seconds", DEFAULT_HEALTH_SECONDS))
            + float(cfg.get("rclone_timeout_seconds", DEFAULT_RCLONE_TIMEOUT))
            + 10.0,
        ),
        "health_staleness_semantics": "advisory; a stale heartbeat alone does not prove daemon death",
        "state": _result_state_counts(cfg),
    }
    with _ACTIVE_LOCK:
        info["active_requests"] = sorted(_ACTIVE)
    info["pending_approvals"] = _pending_approval_ids(cfg)
    info["operator_approval_public_key_pinned"] = bool(cfg.get("operator_approval_public_key_sha256"))
    info["wake_lease"] = _WAKE_LEASE.snapshot() if _WAKE_LEASE is not None else {
        "enabled": bool(cfg.get("wake_lease_enabled", True)), "state": "off", "reason": None,
        "grace_seconds": float(cfg.get("wake_grace_seconds", DEFAULT_WAKE_GRACE_SECONDS)),
    }
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
        info["private_oauth_client_configured"] = _private_oauth_client_configured(cfg)
    return info


def publish_health(cfg: dict) -> None:
    state_dir = Path(cfg["state_dir"]).expanduser()
    payload = doctor(cfg, probe_transport=False)
    payload["kind"] = "shell_bridge_health"
    payload["publisher_pid"] = os.getpid()
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


def _worker(name: str, cfg: dict, active_names: set[str], active_lock: threading.Lock, wake: WakeLease | None = None) -> None:
    try:
        process_one(name, cfg)
    except Exception as exc:
        print(f"request {name}: {type(exc).__name__}: {exc}", flush=True)
    finally:
        with active_lock:
            active_names.discard(name)
            if not active_names and wake is not None:
                wake.enter_idle_grace()


def _signal_shutdown(_signum, _frame):
    _SHUTDOWN.set()
    with _ACTIVE_LOCK:
        pgids = [int(v["pgid"]) for v in _ACTIVE.values() if v.get("pgid")]
    for pgid in pgids:
        _terminate_pgid(pgid)


def daemon(config_path: Path) -> None:
    global _WAKE_LEASE
    cfg = load_config(config_path)
    lock_file = acquire_instance_lock(cfg)
    wake = WakeLease(cfg)
    _WAKE_LEASE = wake
    ensure_remote_dirs(cfg)
    if _result_state_counts(cfg).get("started_without_finished", 0):
        wake.acquire("recovery")
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
            names: list[str] = []
            try:
                names = list_requests_cfg(cfg)
                if names:
                    wake.acquire("request_queue")
                for name in names:
                    if _SHUTDOWN.is_set():
                        break
                    with active_lock:
                        if name in active_names:
                            continue
                        if len(active_names) >= limit:
                            break
                        active_names.add(name)
                    t = threading.Thread(target=_worker, args=(name, cfg, active_names, active_lock, wake), daemon=True)
                    threads.add(t)
                    t.start()
            except Exception as exc:
                print(f"poll error: {type(exc).__name__}: {exc}", flush=True)
            with active_lock:
                if not active_names and not names:
                    wake.enter_idle_grace()
            wake.tick()
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
        wake.close()
        _WAKE_LEASE = None
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
