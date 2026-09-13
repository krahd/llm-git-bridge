from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PROTOCOL_VERSION = 2
DEFAULT_MAX_FILE_BYTES = 1_000_000
DEFAULT_MAX_PATCH_BYTES = 5_000_000
DEFAULT_MAX_SNAPSHOT_BYTES = 8_000_000
DEFAULT_MAX_SNAPSHOT_FILES = 4_000
DEFAULT_MAX_RUN_COMMANDS = 16
TRANSACTION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}")
COMMAND_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
SENSITIVE_BASENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "credentials",
    "credentials.json",
    "secrets",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "auth.json",
    "token.json",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".kdbx", ".jks", ".keystore", ".ovpn", ".tfstate"}
SENSITIVE_EXACT_PATHS = {".docker/config.json", ".kube/config"}
SKIP_PARTS = {".git", ".ssh", ".gnupg", ".aws", "node_modules", ".venv", "venv", "dist", "build", ".cache", "__pycache__"}
PUSH_PROTECTED_PATHS = {
    ".gitmodules",
    ".gitlab-ci.yml",
    ".travis.yml",
    "azure-pipelines.yml",
    "bitbucket-pipelines.yml",
    "jenkinsfile",
}
PUSH_PROTECTED_PREFIXES = {
    ".github/workflows/",
    ".github/actions/",
    ".circleci/",
    ".buildkite/",
}
SENSITIVE_ENV_NAME_RE = re.compile(r"(?:TOKEN|SECRET|PASSWORD|CREDENTIAL|API[_-]?KEY|PRIVATE[_-]?KEY)", re.IGNORECASE)
SENSITIVE_ENV_PREFIXES = ("AWS_", "GOOGLE_", "AZURE_", "GITHUB_", "GH_", "OPENAI_")
SENSITIVE_ENV_EXACT = {
    "SSH_AUTH_SOCK",
    "SSH_AGENT_PID",
    "GIT_ASKPASS",
    "SSH_ASKPASS",
    "KUBECONFIG",
    "DOCKER_CONFIG",
    "CLOUDSDK_CONFIG",
    "GNUPGHOME",
    "RCLONE_CONFIG",
    "RCLONE_CONFIG_PASS",
}
COMMAND_OUTPUT_LIMIT = 256_000
BRIDGE_TX_TRAILER = "LLM-Git-Bridge-Transaction"
BRIDGE_REQUEST_HASH_TRAILER = "LLM-Git-Bridge-Request-SHA256"



class BridgeError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    timeout: float | None = 60,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise BridgeError(f"command timed out after {timeout}s") from exc
    except OSError as exc:
        raise BridgeError(f"command could not be started: {type(exc).__name__}") from exc
    if check and proc.returncode != 0:
        raise BridgeError(f"command failed with exit code {proc.returncode}")
    return proc


def git(repo: Path, *args: str, check: bool = True, timeout: float | None = 60) -> subprocess.CompletedProcess[str]:
    return run(["git", "-C", str(repo), *args], check=check, timeout=timeout)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def strict_json_loads(text: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        obj: dict[str, Any] = {}
        for key, value in pairs:
            if key in obj:
                raise BridgeError(f"duplicate JSON key: {key}")
            obj[key] = value
        return obj

    def reject_constant(value: str) -> Any:
        raise BridgeError(f"invalid JSON constant: {value}")

    try:
        return json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise BridgeError(f"invalid JSON: {exc.msg}") from exc


def load_json(path: Path) -> dict[str, Any]:
    try:
        obj = strict_json_loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise BridgeError(f"cannot read JSON file: {type(exc).__name__}") from exc
    if not isinstance(obj, dict):
        raise BridgeError("JSON document must be an object")
    return obj


def validate_transaction_id(value: Any) -> str:
    if not isinstance(value, str) or not TRANSACTION_ID_RE.fullmatch(value):
        raise BridgeError("invalid transaction_id")
    return value


def transaction_request_sha256(obj: dict[str, Any]) -> str:
    """Hash the complete canonical request for crash-safe replay identity."""
    payload = json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def save_json(path: Path, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, indent=2, sort_keys=True) + "\n")


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-.")
    return value or "repo"


def branch_token(branch: str) -> str:
    base = slugify(branch.replace("/", "--"))[:100]
    digest = hashlib.sha256(branch.encode("utf-8")).hexdigest()[:10]
    return f"{base}-{digest}"


def path_fingerprint(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:8]


def is_git_repo(path: Path) -> bool:
    p = run(["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"], check=False, timeout=10)
    return p.returncode == 0 and p.stdout.strip() == "true"


def discover_repos(roots: Iterable[Path]) -> list[Path]:
    found: set[Path] = set()
    for root in roots:
        root = root.expanduser().resolve()
        if not root.exists() or not root.is_dir():
            continue
        if is_git_repo(root):
            found.add(root)
            continue
        for current, dirs, _files in os.walk(root):
            cur = Path(current)
            dirs[:] = [d for d in dirs if d not in SKIP_PARTS and not d.startswith(".")]
            if (cur / ".git").exists() and is_git_repo(cur):
                found.add(cur.resolve())
                dirs[:] = []
    return sorted(found, key=lambda p: str(p).lower())


def repo_state(path: Path) -> dict[str, Any]:
    """Return HEAD/branch/dirty state with one Git process.

    Porcelain v2 is a stable machine-readable interface and reports both branch
    metadata and tracked/untracked changes. The previous implementation spawned
    four Git processes for every state refresh, which compounded across scans and
    snapshot materialisation.
    """
    raw = git(
        path,
        "status",
        "--porcelain=v2",
        "--branch",
        "-z",
        "--untracked-files=normal",
        timeout=30,
    ).stdout
    head: str | None = None
    branch: str | None = None
    tracked_dirty = False
    untracked = False
    for record in raw.split("\x00"):
        if not record:
            continue
        if record.startswith("# branch.oid "):
            value = record[len("# branch.oid "):].strip()
            if value and value != "(initial)":
                head = value
        elif record.startswith("# branch.head "):
            value = record[len("# branch.head "):].strip()
            if value and value != "(detached)":
                branch = value
        elif record.startswith("? "):
            untracked = True
        elif record.startswith(("1 ", "2 ", "u ")):
            tracked_dirty = True
    if head is None:
        raise BridgeError("repository has no readable HEAD commit")
    return {
        "head": head,
        "branch": branch,
        "tracked_dirty": tracked_dirty,
        "untracked": untracked,
        "dirty": tracked_dirty or untracked,
    }


def build_registry(roots: Iterable[Path], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    repos = discover_repos(roots)
    previous_by_path: dict[str, str] = {}
    if previous:
        for old_id, old_entry in previous.get("repos", {}).items():
            old_path = old_entry.get("path")
            if isinstance(old_path, str):
                previous_by_path[str(Path(old_path).expanduser().resolve())] = old_id

    current_paths = {str(path.resolve()) for path in repos}
    reserved_ids = {
        repo_id for old_path, repo_id in previous_by_path.items() if old_path in current_paths
    }
    entries: dict[str, Any] = {}
    used_ids: set[str] = set()
    for path in repos:
        name = path.name
        resolved = str(path.resolve())
        if resolved in previous_by_path:
            repo_id = previous_by_path[resolved]
        else:
            base_id = slugify(name)
            repo_id = base_id
            unavailable = used_ids | reserved_ids
            if repo_id in unavailable:
                repo_id = f"{base_id}-{path_fingerprint(path)}"
            counter = 2
            candidate = repo_id
            while candidate in unavailable:
                candidate = f"{repo_id}-{counter}"
                counter += 1
            repo_id = candidate
        used_ids.add(repo_id)
        state = repo_state(path)
        entries[repo_id] = {
            "id": repo_id,
            "name": name,
            "path": str(path),
            "head": state["head"],
            "branch": state["branch"],
            "dirty": state["dirty"],
            "tracked_dirty": state["tracked_dirty"],
            "untracked": state["untracked"],
            "last_seen": utc_now(),
        }
    return {"version": 1, "generated_at": utc_now(), "repos": entries}


def public_registry(local_registry: dict[str, Any]) -> dict[str, Any]:
    public_entries = []
    for repo_id, entry in sorted(local_registry.get("repos", {}).items()):
        public_entries.append(
            {
                "id": repo_id,
                "name": entry["name"],
                "head": entry["head"],
                "branch": entry["branch"],
                "dirty": entry["dirty"],
                "tracked_dirty": entry["tracked_dirty"],
                "untracked": entry["untracked"],
                "last_seen": entry["last_seen"],
            }
        )
    return {"protocol": PROTOCOL_VERSION, "generated_at": utc_now(), "repos": public_entries}


def resolve_repo(registry: dict[str, Any], ref: str) -> tuple[str, dict[str, Any]]:
    repos = registry.get("repos", {})
    if ref in repos:
        return ref, repos[ref]
    matches = [(rid, entry) for rid, entry in repos.items() if entry.get("name") == ref]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise BridgeError(f"repository name is ambiguous: {ref}")
    raise BridgeError(f"unknown repository: {ref}")


def _is_sensitive(rel: Path) -> bool:
    posix = rel.as_posix().lower()
    if posix in SENSITIVE_EXACT_PATHS:
        return True
    lowered_parts = tuple(part.lower() for part in rel.parts)
    if any(part in SKIP_PARTS for part in lowered_parts):
        return True
    name = rel.name.lower()
    if name in SENSITIVE_BASENAMES:
        return True
    if rel.suffix.lower() in SENSITIVE_SUFFIXES:
        return True
    if name.startswith(".env."):
        return True
    if name.startswith("service-account") and rel.suffix.lower() == ".json":
        return True
    if name.endswith(".tfstate.backup"):
        return True
    return False


def _contains_obvious_secret(data: bytes) -> bool:
    # Content-level defence for tracked secrets stored under innocuous filenames.
    # Keep this deliberately high-confidence to avoid hiding normal source/test
    # files that merely contain secret *detector literals* or fixtures.
    sample = data[:1_000_000]

    pem_re = re.compile(
        rb"(?ms)^[ \t]*-----BEGIN ((?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY)-----[ \t]*\r?\n"
        rb".+?\r?\n[ \t]*-----END \1-----[ \t]*(?:\r?\n|$)"
    )
    if pem_re.search(sample):
        return True

    # A service-account credential is structured JSON. Looking only for the
    # field-name substrings incorrectly classifies source code that implements
    # this detector (and tests containing example fixtures) as secret content.
    try:
        obj = json.loads(sample.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    service_account_type = "service" + "_account"
    private_key_field = "private" + "_key"
    client_email_field = "client" + "_email"
    private_key_begin = "-----BEGIN " + "PRIVATE KEY-----"
    private_key_end = "-----END " + "PRIVATE KEY-----"
    if not isinstance(obj, dict) or obj.get("type") != service_account_type:
        return False
    private_key = obj.get(private_key_field)
    client_email = obj.get(client_email_field)
    return (
        isinstance(private_key, str)
        and private_key_begin in private_key
        and private_key_end in private_key
        and isinstance(client_email, str)
        and bool(client_email.strip())
    )


def _is_push_protected(rel: Path) -> bool:
    posix = rel.as_posix().lower()
    if posix in PUSH_PROTECTED_PATHS:
        return True
    return any(posix.startswith(prefix) for prefix in PUSH_PROTECTED_PREFIXES)


def _is_probably_binary(data: bytes) -> bool:
    if b"\x00" in data[:8192]:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def list_snapshot_candidates(repo: Path) -> list[Path]:
    proc = git(repo, "ls-files", "-c", "-z")
    raw = proc.stdout.encode("utf-8", errors="surrogateescape")
    out: list[Path] = []
    for token in raw.split(b"\x00"):
        if not token:
            continue
        rel = Path(token.decode("utf-8", errors="surrogateescape"))
        out.append(rel)
    return sorted(set(out), key=lambda p: p.as_posix())


def _snapshot_priority(rel: Path) -> tuple[int, str]:
    name = rel.name.lower()
    if name in {"agents.md", "readme.md", "pyproject.toml", "package.json", "cargo.toml", "go.mod"}:
        return (0, rel.as_posix())
    if rel.parts and rel.parts[0].lower() in {"src", "lib", "app", "docs", "tests"}:
        return (1, rel.as_posix())
    return (2, rel.as_posix())


def build_snapshot(
    repo: Path,
    repo_id: str,
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES,
    max_files: int = DEFAULT_MAX_SNAPSHOT_FILES,
) -> dict[str, Any]:
    repo = repo.resolve()
    state = repo_state(repo)
    included: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    total_bytes = 0

    candidates = sorted(list_snapshot_candidates(repo), key=_snapshot_priority)
    for rel in candidates:
        if _is_sensitive(rel):
            omitted.append({"reason": "sensitive-or-excluded"})
            continue
        path = repo / rel
        try:
            if path.is_symlink():
                omitted.append({"path": rel.as_posix(), "reason": "symlink"})
                continue
            size = path.stat().st_size
            if size > max_file_bytes:
                omitted.append({"path": rel.as_posix(), "reason": "too-large", "bytes": size})
                continue
            if len(included) >= max_files or total_bytes + size > max_total_bytes:
                omitted.append({"path": rel.as_posix(), "reason": "snapshot-budget", "bytes": size})
                continue
            data = path.read_bytes()
        except OSError as exc:
            omitted.append({"path": rel.as_posix(), "reason": f"read-error:{type(exc).__name__}"})
            continue
        if _is_probably_binary(data):
            omitted.append({"path": rel.as_posix(), "reason": "binary", "bytes": len(data)})
            continue
        if _contains_obvious_secret(data):
            omitted.append({"reason": "sensitive-content"})
            continue
        text = data.decode("utf-8")
        total_bytes += len(data)
        included.append(
            {
                "path": rel.as_posix(),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "content": text,
            }
        )

    return {
        "protocol": PROTOCOL_VERSION,
        "kind": "snapshot",
        "repo": repo_id,
        "generated_at": utc_now(),
        "head": state["head"],
        "branch": state["branch"],
        "dirty": state["dirty"],
        "tracked_dirty": state["tracked_dirty"],
        "untracked": state["untracked"],
        "included_files": len(included),
        "included_bytes": total_bytes,
        "snapshot_limits": {
            "max_file_bytes": max_file_bytes,
            "max_total_bytes": max_total_bytes,
            "max_files": max_files,
        },
        "omitted_files": len(omitted),
        "files": included,
        "omitted": omitted,
    }


def validate_safe_branch_name(branch: Any, safe_branch_prefix: str) -> str:
    if not isinstance(branch, str) or not branch.startswith(safe_branch_prefix):
        raise BridgeError(f"branch must start with {safe_branch_prefix!r}")
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", branch):
        raise BridgeError("branch contains unsupported characters")
    parts = branch.split("/")
    if (
        branch.startswith("-")
        or branch.endswith((".", "/"))
        or ".." in branch
        or "//" in branch
        or "@{" in branch
        or any(part.startswith(".") or part.endswith(".lock") for part in parts)
    ):
        raise BridgeError("unsafe branch name")
    return branch


def validate_transaction(obj: dict[str, Any], *, safe_branch_prefix: str, max_patch_bytes: int = DEFAULT_MAX_PATCH_BYTES) -> dict[str, Any]:
    if obj.get("protocol") != PROTOCOL_VERSION:
        raise BridgeError(f"unsupported protocol: {obj.get('protocol')!r}")
    if obj.get("kind") != "transaction":
        raise BridgeError("transaction kind must be 'transaction'")
    txid = validate_transaction_id(obj.get("transaction_id"))
    repo = obj.get("repo")
    if not isinstance(repo, str) or not repo.strip():
        raise BridgeError("missing repo")
    base_sha = obj.get("base_sha")
    if not isinstance(base_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", base_sha):
        raise BridgeError("base_sha must be a 40-character hex SHA")
    branch = validate_safe_branch_name(obj.get("branch"), safe_branch_prefix)
    patch = obj.get("patch")
    if not isinstance(patch, str) or not patch.strip():
        raise BridgeError("patch must be a non-empty string")
    if len(patch.encode("utf-8")) > max_patch_bytes:
        raise BridgeError("patch exceeds maximum allowed size")
    run_names = obj.get("run", [])
    if not isinstance(run_names, list) or not all(
        isinstance(x, str) and COMMAND_NAME_RE.fullmatch(x) for x in run_names
    ):
        raise BridgeError("run must be a list of safe symbolic command names")
    if len(run_names) > DEFAULT_MAX_RUN_COMMANDS:
        raise BridgeError(f"run may contain at most {DEFAULT_MAX_RUN_COMMANDS} commands")
    message = obj.get("commit_message")
    if message is not None and (not isinstance(message, str) or not message.strip() or len(message) > 500):
        raise BridgeError("invalid commit_message")
    if isinstance(message, str) and re.search(
        rf"(?im)^\s*(?:{re.escape(BRIDGE_TX_TRAILER)}|{re.escape(BRIDGE_REQUEST_HASH_TRAILER)})\s*:",
        message,
    ):
        raise BridgeError("commit_message uses a reserved bridge trailer")
    push = obj.get("push", False)
    if not isinstance(push, bool):
        raise BridgeError("push must be a boolean")
    publish_snapshot = obj.get("publish_snapshot", False)
    if not isinstance(publish_snapshot, bool):
        raise BridgeError("publish_snapshot must be a boolean")
    return obj


def _bridge_commit_details(
    repo: Path,
    commit: str,
) -> tuple[str, tuple[str, str] | None, list[str]]:
    """Return resolved OID, authenticated bridge identity, and parents in one Git process."""
    raw = git(repo, "show", "-s", "--format=%H%x00%P%x00%B", commit, timeout=30).stdout
    resolved, separator, remainder = raw.partition("\x00")
    if not separator:
        return "", None, []
    parents_text, separator, body = remainder.partition("\x00")
    if not separator:
        return "", None, []
    parents = parents_text.split()
    tx_matches = re.findall(
        rf"(?im)^{re.escape(BRIDGE_TX_TRAILER)}:\s*([^\s]+)\s*$",
        body,
    )
    hash_matches = re.findall(
        rf"(?im)^{re.escape(BRIDGE_REQUEST_HASH_TRAILER)}:\s*([0-9a-f]{{64}})\s*$",
        body,
    )
    if len(tx_matches) != 1 or len(hash_matches) != 1:
        return resolved.strip().lower(), None, parents
    try:
        txid = validate_transaction_id(tx_matches[0])
    except BridgeError:
        return resolved.strip().lower(), None, parents
    return resolved.strip().lower(), (txid, hash_matches[0]), parents


def _bridge_commit_metadata(
    repo: Path,
    commit: str,
) -> tuple[tuple[str, str] | None, list[str]]:
    """Return authenticated bridge identity and parents with one Git process."""
    _, identity, parents = _bridge_commit_details(repo, commit)
    return identity, parents


def _bridge_commit_identity(repo: Path, commit: str) -> tuple[str, str] | None:
    """Return (transaction_id, request_hash) for an unambiguous bridge commit."""
    return _bridge_commit_metadata(repo, commit)[0]


def _bridge_commit_matches_request(
    repo: Path,
    commit: str,
    *,
    txid: str,
    request_hash: str,
    base_sha: str,
) -> bool:
    identity, parents = _bridge_commit_metadata(repo, commit)
    return (
        identity == (txid, request_hash)
        and len(parents) == 1
        and parents[0].lower() == base_sha.lower()
    )


def branch_tip(repo: Path, branch: str) -> str | None:
    """Return the exact local branch OID, or None when the branch does not exist."""
    proc = git(
        repo,
        "show-ref",
        "--verify",
        "--hash",
        f"refs/heads/{branch}",
        check=False,
        timeout=30,
    )
    if proc.returncode == 0:
        oid = proc.stdout.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40,64}", oid):
            raise BridgeError("could not parse local branch tip")
        return oid
    # `show-ref --verify` returns a non-zero status for a missing exact ref;
    # Git versions differ in the precise status value. This preserves the
    # previous branch_exists() semantics while obtaining the OID on success.
    return None


def branch_exists(repo: Path, branch: str) -> bool:
    return branch_tip(repo, branch) is not None


def commit_exists(repo: Path, sha: str) -> bool:
    return git(repo, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0


def ensure_tracked_clean(repo: Path) -> str:
    raw = git(
        repo,
        "status",
        "--porcelain=v2",
        "--branch",
        "-z",
        "--untracked-files=no",
        timeout=30,
    ).stdout
    staged = False
    unstaged = False
    head_oid = ""
    for record in raw.split("\x00"):
        if record.startswith("# branch.oid "):
            head_oid = record.removeprefix("# branch.oid ").strip().lower()
            continue
        if not record or record[0] not in {"1", "2", "u"}:
            continue
        parts = record.split(" ", 2)
        if len(parts) < 2 or len(parts[1]) != 2:
            raise BridgeError("could not parse tracked repository state")
        x, y = parts[1]
        staged = staged or x != "."
        unstaged = unstaged or y != "."
    if unstaged:
        raise BridgeError("tracked working tree has unstaged modifications")
    if staged:
        raise BridgeError("index has staged modifications")
    if not re.fullmatch(r"[0-9a-f]{40,64}", head_oid):
        raise BridgeError("could not read repository HEAD")
    return head_oid


def retire_worktree(repo: Path, worktree: Path) -> None:
    """Remove one disposable worktree without a routine global prune."""
    if not worktree.exists():
        return
    removed = git(repo, "worktree", "remove", "--force", str(worktree), check=False, timeout=120)
    residual = worktree.exists()
    if residual:
        shutil.rmtree(worktree, ignore_errors=True)
    if removed.returncode != 0 or residual:
        # A failed removal can leave stale registration metadata. Successful
        # removals clean up their own metadata and do not need a global prune.
        git(repo, "worktree", "prune", check=False, timeout=60)


def add_disposable_worktree(repo: Path, *args: str) -> None:
    """Add a worktree, pruning and retrying once only for stale crash metadata."""
    argv = (
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "worktree",
        "add",
        *args,
    )
    first = git(repo, *argv, check=False, timeout=120)
    if first.returncode == 0:
        return
    git(repo, "worktree", "prune", check=False, timeout=60)
    git(repo, *argv, timeout=120)


def ensure_changed_files_are_regular(worktree: Path) -> int:
    # Git's raw diff format reports source mode, destination mode, status, and
    # NUL-safe pathnames in one query. Disable rename/copy detection so source
    # and destination changes cannot hide a protected path behind a rename.
    raw = git(
        worktree,
        "diff",
        "--cached",
        "--raw",
        "-z",
        "--no-renames",
        "--no-abbrev",
        timeout=30,
    ).stdout
    fields = raw.split("\x00")
    allowed_modes = {None, "100644", "100755"}
    index = 0
    changed_count = 0
    while index < len(fields):
        metadata = fields[index]
        index += 1
        if not metadata:
            continue
        changed_count += 1
        if index >= len(fields):
            raise BridgeError("could not parse staged diff metadata")
        path_text = fields[index]
        index += 1
        parts = metadata.split()
        if len(parts) != 5 or not parts[0].startswith(":"):
            raise BridgeError("could not parse staged diff metadata")
        old_mode = parts[0][1:]
        new_mode = parts[1]
        status = parts[4]
        if status not in {"A", "D", "M", "T"}:
            raise BridgeError("unsupported staged change status")
        rel = Path(path_text)
        if _is_sensitive(rel):
            raise BridgeError("transaction touches a protected/sensitive path")
        if _is_push_protected(rel):
            raise BridgeError("transaction touches a protected CI/automation path")

        old_mode = None if old_mode == "000000" else old_mode
        new_mode = None if new_mode == "000000" else new_mode
        if old_mode not in allowed_modes or new_mode not in allowed_modes:
            raise BridgeError("symlink/submodule changes are not allowed")

        path = worktree / rel
        if path.exists() and path.is_symlink():
            raise BridgeError("symlink/submodule changes are not allowed")
    return changed_count


def _repo_control_state(
    worktree: Path,
    *,
    known_head: str | None = None,
    known_head_ref: str | None = None,
    known_index_tree: str | None = None,
) -> dict[str, str]:
    if known_head is None or known_head_ref is None:
        head_lines = git(
            worktree,
            "rev-parse",
            "HEAD",
            "--symbolic-full-name",
            "HEAD",
            timeout=30,
        ).stdout.splitlines()
        if len(head_lines) < 2:
            raise BridgeError("could not read repository HEAD state")
        head = head_lines[0].strip()
        head_ref = head_lines[1].strip()
        if head_ref == "HEAD":
            head_ref = ""
    else:
        head = known_head
        head_ref = known_head_ref
    index_tree = known_index_tree or git(worktree, "write-tree", timeout=30).stdout.strip()
    return {
        "head": head,
        "head_ref": head_ref,
        "index_tree": index_tree,
        "local_config": git(worktree, "config", "--local", "--null", "--list", timeout=30).stdout,
        "refs": git(
            worktree,
            "for-each-ref",
            "--format=%(refname)%00%(objectname)",
            "refs/heads",
            "refs/tags",
            "refs/remotes",
            timeout=30,
        ).stdout,
    }


def _assert_command_invariants(worktree: Path, before: dict[str, str]) -> None:
    after = _repo_control_state(worktree)
    if after != before:
        raise BridgeError("configured command modified Git control state or staged patch")
    if git(worktree, "diff", "--quiet", check=False, timeout=30).returncode != 0:
        raise BridgeError("configured command modified tracked files outside the staged patch")


def _command_argv_for_worktree(argv: list[str], repo: Path, command_workspace: Path) -> list[str]:
    mapped: list[str] = []
    for value in argv:
        candidate = Path(value).expanduser()
        if candidate.is_absolute():
            try:
                rel = candidate.resolve().relative_to(repo)
            except (OSError, ValueError):
                pass
            else:
                mapped.append(str(command_workspace / rel))
                continue
        mapped.append(value)
    return mapped


def _command_environment(command_workspace: Path, private_home: Path | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper in SENSITIVE_ENV_EXACT:
            continue
        if upper.startswith(SENSITIVE_ENV_PREFIXES):
            continue
        if SENSITIVE_ENV_NAME_RE.search(upper):
            continue
        env[key] = value

    # The bridge launcher sets PYTHONPATH to its authoritative checkout. Passing
    # that through would make tests import old code instead of the isolated
    # transaction worktree. Use the worktree's src/ when present; otherwise
    # remove PYTHONPATH entirely.
    env.pop("PYTHONPATH", None)
    src = command_workspace / "src"
    if src.is_dir():
        env["PYTHONPATH"] = str(src)

    # Do not expose the user's normal home/config directories to configured
    # commands by default. This is defence in depth only: commands still run as
    # the local user and are not an OS sandbox.
    if private_home is not None:
        private_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(private_home, 0o700)
        except OSError:
            pass
        tmp = private_home / "tmp"
        tmp.mkdir(parents=True, exist_ok=True, mode=0o700)
        env["HOME"] = str(private_home)
        env["TMPDIR"] = str(tmp)
        env["XDG_CONFIG_HOME"] = str(private_home / ".config")
        env["XDG_CACHE_HOME"] = str(private_home / ".cache")
        env["XDG_DATA_HOME"] = str(private_home / ".local" / "share")
        env["GIT_CONFIG_GLOBAL"] = "/dev/null"
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["PYTHONNOUSERSITE"] = "1"
        env["HISTFILE"] = "/dev/null"
    return env


def _prepare_command_workspace(worktree: Path, state_dir: Path, txid: str) -> tuple[Path, Path]:
    """Create a Git-metadata-free tracked-file copy for configured commands.

    Only tracked files can contribute to the staged transaction and eventual
    commit. Copying the whole worktree made command latency depend on unrelated
    untracked dependency/build trees and exposed those local artefacts to patched
    code. Enumerating the index is both faster and a tighter execution boundary.
    Sensitive paths, symlinks, and high-confidence secret content remain omitted.
    This is still not an OS sandbox: commands execute with the user's account.
    """
    root = state_dir / "command-runs" / txid
    home = state_dir / "command-homes" / txid
    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(home, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)

    for rel in list_snapshot_candidates(worktree):
        if _is_sensitive(rel):
            continue
        source = worktree / rel
        try:
            if source.is_symlink() or not source.is_file():
                continue
            size = source.stat().st_size
            if size <= 1_000_000:
                data = source.read_bytes()
                if _contains_obvious_secret(data):
                    continue
                dest = root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                shutil.copystat(source, dest, follow_symlinks=False)
            else:
                with source.open("rb") as fh:
                    if _contains_obvious_secret(fh.read(1_000_000)):
                        continue
                dest = root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest, follow_symlinks=False)
        except OSError:
            # Fail closed for command inputs that cannot be inspected/copied.
            continue

    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root, home


@dataclass
class ConfiguredCommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def _bounded_append(buf: bytearray, data: bytes, limit: int) -> None:
    if not data:
        return
    buf.extend(data)
    if len(buf) > limit:
        del buf[: len(buf) - limit]


def run_configured_command(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float,
    output_limit: int = COMMAND_OUTPUT_LIMIT,
) -> ConfiguredCommandResult:
    """Run a locally configured command with bounded captured output.

    The command gets its own process group. On timeout, or after its main process
    exits, remaining descendants in that group are terminated so a transaction
    cannot accidentally leave background test servers running indefinitely.
    This is resource containment, not an OS security sandbox.
    """
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        raise BridgeError(f"configured command could not be started: {type(exc).__name__}") from exc

    if proc.stdout is None or proc.stderr is None:
        proc.kill()
        raise BridgeError("configured command output pipes were not created")
    streams = {
        proc.stdout.fileno(): (proc.stdout, bytearray()),
        proc.stderr.fileno(): (proc.stderr, bytearray()),
    }
    selector = selectors.DefaultSelector()
    for fd, (stream, _buf) in streams.items():
        os.set_blocking(fd, False)
        selector.register(stream, selectors.EVENT_READ, data=fd)

    def drain_once(wait: float) -> None:
        if not selector.get_map():
            if wait > 0:
                time.sleep(wait)
            return
        for key, _mask in selector.select(wait):
            fd = key.data
            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                continue
            if chunk:
                _bounded_append(streams[fd][1], chunk, output_limit)
            else:
                selector.unregister(key.fileobj)

    deadline = time.monotonic() + max(0.1, float(timeout))
    timed_out = False
    try:
        while proc.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            drain_once(min(0.1, remaining))

        # The main process is finished (or timed out), but children may remain in
        # the isolated process group. Always retire that group.
        if timed_out:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
        else:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass
            time.sleep(0.05)
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass

        try:
            returncode = proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            returncode = proc.wait(timeout=2)

        drain_deadline = time.monotonic() + 0.25
        while selector.get_map() and time.monotonic() < drain_deadline:
            drain_once(0.05)
    finally:
        selector.close()
        proc.stdout.close()
        proc.stderr.close()

    buffers = list(streams.values())
    stdout = bytes(buffers[0][1]).decode("utf-8", errors="replace")
    stderr = bytes(buffers[1][1]).decode("utf-8", errors="replace")
    return ConfiguredCommandResult(returncode=returncode, stdout=stdout, stderr=stderr, timed_out=timed_out)


def _write_command_log(
    state_dir: Path,
    txid: str,
    index: int,
    name: str,
    proc: ConfiguredCommandResult,
) -> None:
    log_path = state_dir / "command-logs" / txid / f"{index:02d}.log"
    atomic_write_text(
        log_path,
        f"command: {name}\nreturncode: {proc.returncode}\n\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}\n",
    )


@dataclass
class TransactionOutcome:
    result: dict[str, Any]
    snapshot: dict[str, Any] | None = None


def process_transaction(
    repo: Path,
    repo_id: str,
    tx: dict[str, Any],
    *,
    state_dir: Path,
    safe_branch_prefix: str,
    commands: dict[str, list[str]] | None = None,
    allow_commit: bool = True,
    allow_push: bool = False,
) -> TransactionOutcome:
    started = time.monotonic()
    timings: dict[str, float] = {}

    def mark(name: str, since: float) -> float:
        now = time.monotonic()
        timings[name] = round(now - since, 4)
        return now

    tx = validate_transaction(tx, safe_branch_prefix=safe_branch_prefix)
    txid = tx["transaction_id"]
    branch = tx["branch"]
    base_sha = tx["base_sha"].lower()
    request_hash = transaction_request_sha256(tx)
    repo = repo.resolve()
    current_head = ensure_tracked_clean(repo)

    push_requested = bool(tx.get("push", False))
    publish_snapshot_requested = bool(tx.get("publish_snapshot", False))

    tip = branch_tip(repo, branch)
    existing = tip is not None
    recovered_commit: str | None = None
    if existing:
        assert tip is not None
        if tip != base_sha:
            identity, parents = _bridge_commit_metadata(repo, tip)
            if identity is not None and identity[0] == txid and identity[1] != request_hash:
                raise BridgeError("transaction_id was already committed with a different request")
            if (
                identity == (txid, request_hash)
                and len(parents) == 1
                and parents[0].lower() == base_sha
            ):
                # The previous daemon may have died after git commit made the
                # branch durable but before its local/remote result was saved.
                # The reserved trailers bind the commit to the complete request,
                # so resuming here is idempotent and does not re-run patch/tests.
                recovered_commit = tip
            else:
                raise BridgeError(f"stale branch base: {branch} is at {tip}, transaction expects {base_sha}")
    else:
        if current_head != base_sha:
            raise BridgeError(f"stale repository base: HEAD is {current_head}, transaction expects {base_sha}")

    if recovered_commit is None and not allow_commit:
        raise BridgeError("commits are disabled locally for this repository")
    if recovered_commit is None and push_requested and not allow_push:
        raise BridgeError("push requested but is not enabled locally for this repository")

    worktrees = state_dir / "worktrees"
    worktrees.mkdir(parents=True, exist_ok=True)
    wt = worktrees / txid

    # A process crash can leave both the directory and Git's worktree metadata.
    # Retire both before resuming so a durable bridge commit can be recovered.
    retire_worktree(repo, wt)

    t = time.monotonic()
    created_new_branch = not existing
    commit_created = recovered_commit is not None
    if existing:
        add_disposable_worktree(repo, str(wt), branch)
    else:
        add_disposable_worktree(repo, "-b", branch, str(wt), base_sha)
    t = mark("worktree_create_s", t)

    snapshot: dict[str, Any] | None = None
    command_workspace: Path | None = None
    command_home: Path | None = None
    try:
        command_results: list[dict[str, Any]] = []
        if recovered_commit is not None:
            commit_sha = recovered_commit
            command_results = [
                {"name": name, "returncode": 0, "recovered": True}
                for name in tx.get("run", [])
            ]
            timings["recovery_s"] = round(time.monotonic() - t, 4)
            t = time.monotonic()
        else:
            patch_path = state_dir / "transactions" / txid / "change.patch"
            atomic_write_text(patch_path, tx["patch"])

            apply_proc = run(
                ["git", "-C", str(wt), "apply", "--index", str(patch_path)],
                check=False,
                timeout=60,
            )
            if apply_proc.returncode != 0:
                raise BridgeError("git apply failed")
            diff_check = run(
                ["git", "-C", str(wt), "diff", "--cached", "--check"],
                check=False,
                timeout=60,
            )
            if diff_check.returncode != 0:
                raise BridgeError("git diff --check failed")
            changed_count = ensure_changed_files_are_regular(wt)
            if changed_count == 0:
                raise BridgeError("patch produced no changes")
            t = mark("patch_apply_s", t)

            allowed_commands = commands or {}
            command_state: dict[str, str] | None = None
            if tx.get("run"):
                staged_tree = git(wt, "write-tree", timeout=30).stdout.strip()
                command_state = _repo_control_state(
                    wt,
                    known_head=base_sha,
                    known_head_ref=f"refs/heads/{branch}",
                    known_index_tree=staged_tree,
                )
                command_workspace, command_home = _prepare_command_workspace(wt, state_dir, txid)
            for index, name in enumerate(tx.get("run", []), start=1):
                argv = allowed_commands.get(name)
                if argv is None:
                    raise BridgeError(f"requested command is not configured locally: {name}")
                if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
                    raise BridgeError(f"configured command {name!r} is invalid")
                command_started = time.monotonic()
                assert command_workspace is not None and command_home is not None
                command_argv = _command_argv_for_worktree(argv, repo, command_workspace)
                proc = run_configured_command(
                    command_argv,
                    cwd=command_workspace,
                    timeout=600,
                    env=_command_environment(command_workspace, command_home),
                )
                _write_command_log(state_dir, txid, index, name, proc)
                command_results.append(
                    {
                        "name": name,
                        "returncode": proc.returncode,
                        "duration_s": round(time.monotonic() - command_started, 4),
                    }
                )
                if proc.timed_out:
                    raise BridgeError(f"configured command timed out: {name}")
                if proc.returncode != 0:
                    raise BridgeError(f"configured command failed: {name}")
                assert command_state is not None
                _assert_command_invariants(wt, command_state)
            t = mark("commands_s", t)

            message = tx.get("commit_message") or f"Apply bridge transaction {txid}"
            trailer_block = (
                f"{BRIDGE_TX_TRAILER}: {txid}\n"
                f"{BRIDGE_REQUEST_HASH_TRAILER}: {request_hash}"
            )
            run(
                [
                    "git",
                    "-C",
                    str(wt),
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "commit.gpgSign=false",
                    "commit",
                    "-m",
                    message,
                    "-m",
                    trailer_block,
                ],
                timeout=120,
            )
            commit_created = True
            commit_sha, identity, parents = _bridge_commit_details(wt, "HEAD")
            if not (
                identity == (txid, request_hash)
                and len(parents) == 1
                and parents[0].lower() == base_sha
            ):
                raise BridgeError("bridge commit identity invariant failed")
            t = mark("commit_s", t)

        push_result: dict[str, Any] | None = None
        if push_requested:
            push_started = time.monotonic()
            ref = f"refs/heads/{branch}"
            if not allow_push:
                # A crash-recovered commit is already durable. A later local policy
                # change may disable pushing; preserve commit success without violating
                # the current opt-in policy.
                push_result = {
                    "requested": True,
                    "status": "error",
                    "remote": "origin",
                    "ref": ref,
                    "error": "push is no longer enabled locally",
                }
                timings["push_s"] = round(time.monotonic() - push_started, 4)
            else:
                try:
                    push_proc = run(
                        [
                            "git",
                            "-C",
                            str(wt),
                            "-c",
                            "core.hooksPath=/dev/null",
                            "push",
                            "--porcelain",
                            "origin",
                            f"{ref}:{ref}",
                        ],
                        check=False,
                        timeout=120,
                        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                    )
                    if push_proc.returncode == 0:
                        push_result = {
                            "requested": True,
                            "status": "success",
                            "remote": "origin",
                            "ref": ref,
                        }
                    else:
                        push_result = {
                            "requested": True,
                            "status": "error",
                            "remote": "origin",
                            "ref": ref,
                            "error": f"git push failed with exit code {push_proc.returncode}",
                        }
                except BridgeError:
                    # The commit is already durable locally. Network/process failure is
                    # secondary and must not turn the transaction itself into failure.
                    push_result = {
                        "requested": True,
                        "status": "error",
                        "remote": "origin",
                        "ref": ref,
                        "error": "git push failed or timed out",
                    }
                timings["push_s"] = round(time.monotonic() - push_started, 4)


        snapshot_error = False
        if publish_snapshot_requested:
            try:
                snapshot = build_snapshot(wt, repo_id)
            except Exception:
                # Snapshotting is secondary to the already-created commit. Do not
                # expose filesystem or command details in a remote result.
                snapshot_error = True

        timings["total_local_s"] = round(time.monotonic() - started, 4)
        result = {
            "protocol": PROTOCOL_VERSION,
            "kind": "result",
            "transaction_id": txid,
            "repo": repo_id,
            "status": "success",
            "base_sha": base_sha,
            "branch": branch,
            "commit": commit_sha,
            "processed_at": utc_now(),
            "commands": command_results,
            "timings": timings,
        }
        if recovered_commit is not None:
            result["recovered_after_crash"] = True
        if push_result is not None:
            result["push"] = push_result
        if publish_snapshot_requested:
            if snapshot_error:
                result["snapshot_error"] = "snapshot generation failed"
        else:
            result["snapshot_deferred"] = True
        return TransactionOutcome(result=result, snapshot=snapshot)
    finally:
        # Remove the worktree but keep a successfully created branch/commit.
        retire_worktree(repo, wt)
        if command_workspace is not None:
            shutil.rmtree(command_workspace, ignore_errors=True)
        if command_home is not None:
            shutil.rmtree(command_home, ignore_errors=True)
        if created_new_branch and not commit_created:
            git(repo, "branch", "-D", branch, check=False, timeout=60)
