from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
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
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
SKIP_PARTS = {".git", ".ssh", ".gnupg", ".aws", "node_modules", ".venv", "venv", "dist", "build", ".cache", "__pycache__"}


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
        raise BridgeError(f"command timed out after {timeout}s: {argv!r}") from exc
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise BridgeError(f"command failed ({proc.returncode}): {argv!r}: {detail}")
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


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeError(f"cannot read JSON {path}: {exc}") from exc


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
    head = git(path, "rev-parse", "HEAD").stdout.strip()
    branch = git(path, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).stdout.strip() or None
    tracked_dirty = git(path, "diff", "--quiet", check=False).returncode != 0 or git(
        path, "diff", "--cached", "--quiet", check=False
    ).returncode != 0
    untracked = bool(git(path, "ls-files", "--others", "--exclude-standard").stdout.strip())
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
    if any(part in SKIP_PARTS for part in rel.parts):
        return True
    name = rel.name.lower()
    if name in SENSITIVE_BASENAMES:
        return True
    if rel.suffix.lower() in SENSITIVE_SUFFIXES:
        return True
    if name.startswith(".env."):
        return True
    return False


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
            omitted.append({"path": rel.as_posix(), "reason": "sensitive-or-excluded"})
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


def validate_transaction(obj: dict[str, Any], *, safe_branch_prefix: str, max_patch_bytes: int = DEFAULT_MAX_PATCH_BYTES) -> dict[str, Any]:
    if obj.get("protocol") != PROTOCOL_VERSION:
        raise BridgeError(f"unsupported protocol: {obj.get('protocol')!r}")
    if obj.get("kind") != "transaction":
        raise BridgeError("transaction kind must be 'transaction'")
    txid = obj.get("transaction_id")
    if not isinstance(txid, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", txid):
        raise BridgeError("invalid transaction_id")
    repo = obj.get("repo")
    if not isinstance(repo, str) or not repo.strip():
        raise BridgeError("missing repo")
    base_sha = obj.get("base_sha")
    if not isinstance(base_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", base_sha):
        raise BridgeError("base_sha must be a 40-character hex SHA")
    branch = obj.get("branch")
    if not isinstance(branch, str) or not branch.startswith(safe_branch_prefix):
        raise BridgeError(f"branch must start with {safe_branch_prefix!r}")
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", branch):
        raise BridgeError("branch contains unsupported characters")
    if (
        branch.startswith("-")
        or branch.endswith((".", "/", ".lock"))
        or ".." in branch
        or "//" in branch
        or "@{" in branch
        or "/." in branch
    ):
        raise BridgeError("unsafe branch name")
    if run(["git", "check-ref-format", f"refs/heads/{branch}"], check=False, timeout=10).returncode != 0:
        raise BridgeError("branch is not a valid Git branch name")
    patch = obj.get("patch")
    if not isinstance(patch, str) or not patch.strip():
        raise BridgeError("patch must be a non-empty string")
    if len(patch.encode("utf-8")) > max_patch_bytes:
        raise BridgeError("patch exceeds maximum allowed size")
    run_names = obj.get("run", [])
    if not isinstance(run_names, list) or not all(isinstance(x, str) and x for x in run_names):
        raise BridgeError("run must be a list of command names")
    message = obj.get("commit_message")
    if message is not None and (not isinstance(message, str) or not message.strip() or len(message) > 500):
        raise BridgeError("invalid commit_message")
    return obj


def branch_exists(repo: Path, branch: str) -> bool:
    return git(repo, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode == 0


def commit_exists(repo: Path, sha: str) -> bool:
    return git(repo, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0


def ensure_tracked_clean(repo: Path) -> None:
    if git(repo, "diff", "--quiet", check=False).returncode != 0:
        raise BridgeError("tracked working tree has unstaged modifications")
    if git(repo, "diff", "--cached", "--quiet", check=False).returncode != 0:
        raise BridgeError("index has staged modifications")


def ensure_changed_files_are_regular(worktree: Path) -> None:
    # The patch is staged with git apply --index, so inspect the staged diff.
    raw = git(worktree, "diff", "--cached", "--raw").stdout
    for line in raw.splitlines():
        if not line.startswith(":"):
            continue
        meta = line.split("\t", 1)[0].split()
        if len(meta) < 5:
            continue
        old_mode = meta[0][1:]
        new_mode = meta[1]
        if new_mode not in {"000000", "100644", "100755"}:
            raise BridgeError(
                f"transaction changes a non-regular file mode ({old_mode} -> {new_mode}); symlink/submodule changes are not allowed"
            )

    changed = [x for x in git(worktree, "diff", "--cached", "--name-only", "-z").stdout.split("\x00") if x]
    for rel_text in changed:
        rel = Path(rel_text)
        if _is_sensitive(rel):
            raise BridgeError(f"transaction touches a protected/sensitive path: {rel.as_posix()}")
        path = worktree / rel
        if path.exists() and path.is_symlink():
            raise BridgeError(f"transaction creates or modifies a symlink: {rel.as_posix()}")

def sanitize_command_result(proc: subprocess.CompletedProcess[str], limit: int = 12_000) -> dict[str, Any]:
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout[-limit:],
        "stderr": proc.stderr[-limit:],
    }


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
    repo = repo.resolve()
    ensure_tracked_clean(repo)

    if not commit_exists(repo, base_sha):
        raise BridgeError(f"base commit does not exist locally: {base_sha}")

    existing = branch_exists(repo, branch)
    if existing:
        tip = git(repo, "rev-parse", branch).stdout.strip().lower()
        if tip != base_sha:
            raise BridgeError(f"stale branch base: {branch} is at {tip}, transaction expects {base_sha}")
    else:
        current_head = git(repo, "rev-parse", "HEAD").stdout.strip().lower()
        if current_head != base_sha:
            raise BridgeError(f"stale repository base: HEAD is {current_head}, transaction expects {base_sha}")

    worktrees = state_dir / "worktrees"
    worktrees.mkdir(parents=True, exist_ok=True)
    wt = worktrees / txid
    if wt.exists():
        shutil.rmtree(wt)

    t = time.monotonic()
    created_new_branch = not existing
    commit_created = False
    if existing:
        git(repo, "-c", "core.hooksPath=/dev/null", "worktree", "add", str(wt), branch, timeout=120)
    else:
        git(repo, "-c", "core.hooksPath=/dev/null", "worktree", "add", "-b", branch, str(wt), base_sha, timeout=120)
    t = mark("worktree_create_s", t)

    snapshot: dict[str, Any] | None = None
    try:
        patch_path = state_dir / "transactions" / txid / "change.patch"
        atomic_write_text(patch_path, tx["patch"])

        check_apply = run(["git", "-C", str(wt), "apply", "--check", "--index", str(patch_path)], check=False, timeout=60)
        if check_apply.returncode != 0:
            raise BridgeError("git apply --check failed: " + (check_apply.stderr.strip() or check_apply.stdout.strip()))

        run(["git", "-C", str(wt), "apply", "--index", str(patch_path)], timeout=60)
        diff_check = run(["git", "-C", str(wt), "diff", "--cached", "--check"], check=False, timeout=60)
        if diff_check.returncode != 0:
            raise BridgeError("git diff --check failed: " + (diff_check.stderr.strip() or diff_check.stdout.strip()))
        ensure_changed_files_are_regular(wt)
        t = mark("patch_apply_s", t)

        allowed_commands = commands or {}
        command_results: list[dict[str, Any]] = []
        for name in tx.get("run", []):
            argv = allowed_commands.get(name)
            if argv is None:
                raise BridgeError(f"requested command is not configured locally: {name}")
            if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
                raise BridgeError(f"configured command {name!r} is invalid")
            proc = run(argv, cwd=wt, check=False, timeout=600)
            item = {"name": name, **sanitize_command_result(proc)}
            command_results.append(item)
            if proc.returncode != 0:
                raise BridgeError(f"configured command failed: {name}")
        if git(wt, "diff", "--quiet", check=False).returncode != 0:
            raise BridgeError("configured command modified tracked files outside the staged patch")
        t = mark("commands_s", t)

        staged = run(["git", "-C", str(wt), "diff", "--cached", "--name-only"], timeout=60).stdout
        if not staged.strip():
            raise BridgeError("patch produced no changes")

        commit_sha = None
        if allow_commit:
            message = tx.get("commit_message") or f"Apply bridge transaction {txid}"
            run(
                [
                    "git", "-C", str(wt),
                    "-c", "core.hooksPath=/dev/null",
                    "-c", "commit.gpgSign=false",
                    "commit", "-m", message,
                ],
                timeout=120,
            )
            commit_sha = run(["git", "-C", str(wt), "rev-parse", "HEAD"], timeout=30).stdout.strip()
            commit_created = True
            # Tests may have left untracked artifacts. The worktree is disposable;
            # remove them so the published snapshot describes only the commit.
            run(["git", "-C", str(wt), "clean", "-ffd"], check=False, timeout=60)
        t = mark("commit_s", t)

        snapshot = build_snapshot(wt, repo_id)
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
        return TransactionOutcome(result=result, snapshot=snapshot)
    finally:
        # Remove the worktree but keep the branch/commit. Prune stale metadata if necessary.
        git(repo, "worktree", "remove", "--force", str(wt), check=False, timeout=120)
        if wt.exists():
            shutil.rmtree(wt, ignore_errors=True)
        git(repo, "worktree", "prune", check=False, timeout=60)
        if created_new_branch and not commit_created:
            git(repo, "branch", "-D", branch, check=False, timeout=60)
