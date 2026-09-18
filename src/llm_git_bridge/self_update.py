from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

from .core import BridgeError, atomic_write_text, load_json, run, save_json, utc_now

APP_NAME = "llm-git-bridge"
STATE_DIR = Path.home() / ".local" / "state" / APP_NAME
CONFIG_DIR = Path.home() / ".config" / APP_NAME
INSTALL_ROOT = Path.home() / ".local" / "share" / APP_NAME
RUNTIMES_DIR = INSTALL_ROOT / "runtimes"
ACTIVE_RUNTIME = INSTALL_ROOT / "active-runtime.json"
UPDATER_CONFIG = CONFIG_DIR / "self-update.json"
HANDOFF_DIR = STATE_DIR / "self-update"
HANDOFF_PATH = HANDOFF_DIR / "handoff.json"
JOURNAL_PATH = HANDOFF_DIR / "journal.json"
LOCK_PATH = HANDOFF_DIR / "supervisor.lock"
HEALTH_PATH = STATE_DIR / "runtime-health.json"
RUNTIME_LAUNCHER = Path.home() / ".local" / "bin" / "llm-git-bridge-runtime"
UPDATER_LAUNCHER = Path.home() / ".local" / "bin" / "llm-git-bridge-self-update"
LABEL = "io.llm-git-bridge.daemon"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _sha(value: Any) -> str:
    if not isinstance(value, str) or len(value) not in {40, 64} or any(c not in "0123456789abcdef" for c in value):
        raise BridgeError("self-update target_sha must be a full lowercase Git object ID")
    return value


def qualification_path(sha: str) -> Path:
    return STATE_DIR / "qualifications" / f"{_sha(sha)}.json"


def record_qualification(*, sha: str, transaction_id: str, request_sha256: str | None, branch: str) -> None:
    sha = _sha(sha)
    target = qualification_path(sha)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    save_json(target, {
        "version": 1,
        "commit": sha,
        "transaction_id": transaction_id,
        "request_sha256": request_sha256,
        "branch": branch,
        "qualified_at": utc_now(),
    })
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass


def require_qualification(sha: str) -> dict[str, Any]:
    path = qualification_path(sha)
    if not path.exists():
        raise BridgeError("self-update target has no local full-test qualification receipt")
    obj = load_json(path)
    if obj.get("version") != 1 or obj.get("commit") != sha or not isinstance(obj.get("transaction_id"), str):
        raise BridgeError("self-update qualification receipt is invalid")
    return obj


def write_handoff(*, transaction_id: str, target_sha: str, source_branch: str, source_repo: Path) -> dict[str, Any]:
    target_sha = _sha(target_sha)
    require_qualification(target_sha)
    HANDOFF_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    handoff = {
        "version": 1,
        "transaction_id": transaction_id,
        "target_sha": target_sha,
        "source_branch": source_branch,
        "source_repo": str(source_repo.expanduser().resolve()),
        "created_at": utc_now(),
    }
    save_json(HANDOFF_PATH, handoff)
    try:
        os.chmod(HANDOFF_PATH, 0o600)
    except OSError:
        pass
    return handoff


def _active_runtime() -> dict[str, Any]:
    if not ACTIVE_RUNTIME.exists():
        raise BridgeError("self-update runtime pointer is not installed")
    obj = load_json(ACTIVE_RUNTIME)
    sha = _sha(obj.get("sha"))
    path = obj.get("path")
    if not isinstance(path, str) or not path:
        raise BridgeError("self-update runtime pointer is invalid")
    return {"sha": sha, "path": path}


def _write_active(sha: str, path: Path) -> None:
    ACTIVE_RUNTIME.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    save_json(ACTIVE_RUNTIME, {"version": 1, "sha": _sha(sha), "path": str(path)})


def _git(repo: Path, *args: str, check: bool = True, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    return run(["git", "-C", str(repo), *args], check=check, timeout=timeout)


def _tracked_clean(repo: Path) -> bool:
    proc = _git(repo, "status", "--porcelain", "--untracked-files=no", check=False, timeout=30)
    return proc.returncode == 0 and not proc.stdout.strip()


def _branch(repo: Path) -> str:
    proc = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False, timeout=30)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise BridgeError("self-update source repository must have a checked-out branch")
    return proc.stdout.strip()


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD", timeout=30).stdout.strip()


def _ensure_commit(repo: Path, sha: str) -> None:
    proc = _git(repo, "cat-file", "-e", f"{sha}^{{commit}}", check=False, timeout=30)
    if proc.returncode != 0:
        raise BridgeError("self-update target commit is not present in the authoritative repository")


def _install_runtime(repo: Path, sha: str) -> Path:
    RUNTIMES_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    final = RUNTIMES_DIR / sha
    if final.exists():
        return final
    tmp = Path(tempfile.mkdtemp(prefix=f"{sha}.tmp-", dir=str(RUNTIMES_DIR)))
    try:
        archive = subprocess.Popen(
            ["git", "-C", str(repo), "archive", "--format=tar", sha],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert archive.stdout is not None
        try:
            with tarfile.open(fileobj=archive.stdout, mode="r|") as tf:
                tf.extractall(tmp)
        finally:
            archive.stdout.close()
        stderr = archive.stderr.read().decode("utf-8", errors="replace") if archive.stderr is not None else ""
        rc = archive.wait(timeout=60)
        if rc != 0:
            raise BridgeError("self-update could not materialise the qualified runtime")
        os.replace(tmp, final)
        return final
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _launchctl(*args: str, check: bool = True) -> int:
    proc = run(["launchctl", *args], check=False, timeout=30)
    if check and proc.returncode != 0:
        raise BridgeError("launchctl operation failed during self-update")
    return proc.returncode



def _promote_local_main(repo: Path, target: str, expected_head: str, transaction_id: str) -> None:
    if _branch(repo) != "main" or _head(repo) != expected_head or not _tracked_clean(repo):
        raise BridgeError("self-update source repository changed before local promotion")
    merge = _git(repo, "-c", "core.hooksPath=/dev/null", "merge", "--ff-only", "--no-edit", target, check=False, timeout=120)
    if merge.returncode != 0:
        raise BridgeError("self-update could not fast-forward local main")
    current_branch = _branch(repo)
    current_head = _head(repo)
    if current_branch == "main" and current_head == target and _tracked_clean(repo):
        return
    # If a human switched branches in the narrow pre-merge/Git-lock race, the
    # merge may have fast-forwarded that other branch. Repair only the exact
    # target commit and only while the checkout is still tracked-clean. Use the
    # branch reflog to recover its actual previous tip rather than assuming it
    # shared main's base.
    if current_branch != "main" and current_head == target and _tracked_clean(repo):
        reflog = _git(repo, "reflog", "show", "--format=%H", "-2", f"refs/heads/{current_branch}", check=False, timeout=30)
        tips = [line.strip() for line in reflog.stdout.splitlines() if line.strip()]
        if reflog.returncode == 0 and len(tips) >= 2 and tips[0] == target:
            previous = tips[1]
            rollback = _git(
                repo, "update-ref", "--no-deref", "-m", f"llm-git-bridge rollback {transaction_id}",
                f"refs/heads/{current_branch}", previous, target, check=False, timeout=60,
            )
            if rollback.returncode == 0 and _branch(repo) == current_branch:
                reset = _git(repo, "reset", "--hard", previous, check=False, timeout=60)
                if reset.returncode != 0:
                    raise BridgeError("self-update branch-switch rollback requires manual inspection")
    raise BridgeError("self-update current branch changed during local promotion")

def _restart_daemon() -> None:
    if not PLIST_PATH.exists():
        raise BridgeError("self-update requires the installed macOS LaunchAgent")
    domain = f"gui/{os.getuid()}"
    service = f"{domain}/{LABEL}"
    if _launchctl("kill", "SIGTERM", service, check=False) != 0:
        if _launchctl("kickstart", service, check=False) != 0:
            _launchctl("bootstrap", domain, str(PLIST_PATH))


def _wait_health(sha: str, *, timeout_s: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if HEALTH_PATH.exists():
            try:
                obj = load_json(HEALTH_PATH)
                if obj.get("runtime_sha") == sha and isinstance(obj.get("watcher_started_at"), str):
                    return True
            except BridgeError:
                pass
        time.sleep(0.25)
    return False


def _journal(**fields: Any) -> None:
    HANDOFF_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    current: dict[str, Any] = {}
    if JOURNAL_PATH.exists():
        try:
            current = load_json(JOURNAL_PATH)
        except BridgeError:
            current = {}
    current.update(fields)
    current["updated_at"] = utc_now()
    save_json(JOURNAL_PATH, current)


def run_supervisor(handoff_path: Path | None = None) -> int:
    import fcntl

    if handoff_path is None:
        handoff_path = HANDOFF_PATH
    HANDOFF_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_fh = LOCK_PATH.open("a+")
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    try:
        handoff = load_json(handoff_path)
        if handoff.get("version") != 1:
            raise BridgeError("self-update handoff is invalid")
        target = _sha(handoff.get("target_sha"))
        source_branch = handoff.get("source_branch")
        source_repo_raw = handoff.get("source_repo")
        if not isinstance(source_branch, str) or not source_branch.startswith("ai/"):
            raise BridgeError("self-update source branch must be a safe branch")
        if not isinstance(source_repo_raw, str) or not source_repo_raw:
            raise BridgeError("self-update source repository is invalid")
        source_repo = Path(source_repo_raw).expanduser().resolve()
        require_qualification(target)
        previous = _active_runtime()
        _ensure_commit(source_repo, target)
        if not _tracked_clean(source_repo):
            raise BridgeError("self-update source repository has tracked local modifications")
        if _branch(source_repo) != "main":
            raise BridgeError("self-update source repository must remain on main")
        local_head = _head(source_repo)
        if _git(source_repo, "merge-base", "--is-ancestor", local_head, target, check=False, timeout=30).returncode != 0:
            raise BridgeError("self-update target is not a fast-forward descendant of current main")
        source_tip = _git(source_repo, "rev-parse", f"refs/heads/{source_branch}", timeout=30).stdout.strip()
        if source_tip != target:
            raise BridgeError("self-update safe branch no longer points at the qualified target")

        runtime = _install_runtime(source_repo, target)
        _journal(transaction_id=handoff.get("transaction_id"), target_sha=target, previous_sha=previous["sha"], runtime_installed=True)

        remote_main = _git(source_repo, "ls-remote", "--heads", "origin", "refs/heads/main", timeout=30).stdout.strip().split()
        remote_oid = remote_main[0] if remote_main else None
        if remote_oid != target:
            push = _git(source_repo, "push", "origin", f"{target}:refs/heads/main", check=False, timeout=120)
            if push.returncode != 0:
                raise BridgeError("self-update could not fast-forward origin/main")
        _journal(remote_promoted=True)

        if _head(source_repo) != target:
            _promote_local_main(source_repo, target, local_head, str(handoff.get("transaction_id") or "self-update"))
        _journal(local_promoted=True)

        _write_active(target, runtime)
        HEALTH_PATH.unlink(missing_ok=True)
        _journal(active_runtime_switched=True)
        _restart_daemon()
        if not _wait_health(target):
            previous_path = Path(previous["path"])
            _write_active(previous["sha"], previous_path)
            HEALTH_PATH.unlink(missing_ok=True)
            _journal(rollback_started=True)
            _restart_daemon()
            if not _wait_health(previous["sha"]):
                _journal(status="rollback-health-failed")
                raise BridgeError("self-update target failed health check and rollback health check failed")
            _journal(status="rolled-back")
            raise BridgeError("self-update target failed health check; previous runtime restored")
        _journal(status="success", health_verified=True)
        handoff_path.unlink(missing_ok=True)
        return 0
    finally:
        lock_fh.close()


def stable_runtime_launcher_text() -> str:
    return '''#!/usr/bin/env python3\nimport json, os\nfrom pathlib import Path\nroot=Path.home()/".local/share/llm-git-bridge"\nobj=json.loads((root/"active-runtime.json").read_text())\nos.environ["LLM_GIT_BRIDGE_RUNTIME_SHA"]=obj["sha"]\nprogram=Path(obj["path"])/"bin/llm-git-bridge"\nos.execv(str(program), [str(program), *os.sys.argv[1:]])\n'''


def stable_updater_launcher_text() -> str:
    return '''#!/usr/bin/env python3\nimport json, os\nfrom pathlib import Path\nroot=Path.home()/".local/share/llm-git-bridge"\nobj=json.loads((root/"active-runtime.json").read_text())\nos.environ["LLM_GIT_BRIDGE_RUNTIME_SHA"]=obj["sha"]\nsrc=str(Path(obj["path"])/"src")\nos.environ["PYTHONPATH"]=src+(os.pathsep+os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")\nos.execv(os.sys.executable, [os.sys.executable, "-m", "llm_git_bridge.self_update", "--supervise"])\n'''


def install_stable_launchers(*, source_repo: Path, active_sha: str) -> Path:
    active_sha = _sha(active_sha)
    _ensure_commit(source_repo, active_sha)
    runtime = _install_runtime(source_repo, active_sha)
    for path, text in ((RUNTIME_LAUNCHER, stable_runtime_launcher_text()), (UPDATER_LAUNCHER, stable_updater_launcher_text())):
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, text)
        path.chmod(0o755)
    UPDATER_CONFIG.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    save_json(UPDATER_CONFIG, {"version": 1, "source_repo": str(source_repo.expanduser().resolve())})
    _write_active(active_sha, runtime)
    return runtime


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--supervise", action="store_true")
    args = p.parse_args(argv)
    if args.supervise:
        return run_supervisor()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
