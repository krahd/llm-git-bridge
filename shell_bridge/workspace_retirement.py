"""Fail-closed local lifecycle inventory and exact-job retirement for bridge v6.

NEVER callable by a remote bridge request. Deletion requires a local TTY,
explicit job identity, an unloaded production LaunchAgent, and fresh checks.
Receipts, local branches, remote checkpoint branches, and LFS objects survive.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

HASH = re.compile(r"[0-9a-f]{40}\Z")
LFS_POINTER = re.compile(
    rb"version https://git-lfs.github.com/spec/v1\noid sha256:([0-9a-f]{64})\nsize ([0-9]+)\n?\Z"
)
MAX_DS_STORE_BYTES = 16 * 1024 * 1024
DEFAULT_RETENTION_DAYS = 14


def _need(w, ok: bool, reason: str) -> None:
    if not ok:
        raise w.WorkspaceError(reason)


def _bytes(argv: list[str], *, input_data: bytes | None = None, timeout: int = 180) -> bytes:
    try:
        cp = subprocess.run(argv, input=input_data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"inspection unavailable: {argv[0]}") from exc
    if cp.returncode != 0 or cp.stderr:
        raise RuntimeError(f"inspection failed: {argv[0]} rc={cp.returncode}")
    return cp.stdout


def _git_bytes(repo: Path, *args: str, input_data: bytes | None = None) -> bytes:
    return _bytes(["git", "-C", str(repo), *args], input_data=input_data)


def _hash_file(w, path: Path, expected_sha: str, expected_bytes: int) -> None:
    try:
        st = path.lstat()
        _need(w, stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and st.st_size == expected_bytes,
              "LFS payload missing, linked or wrong size")
        sha = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                sha.update(chunk)
    except OSError as exc:
        raise w.WorkspaceError("LFS payload missing or unreadable") from exc
    _need(w, sha.hexdigest() == expected_sha, "LFS payload content hash mismatch")


def _safe_finder_xattrs(w, path: Path) -> None:
    if platform.system() == "Darwin":
        _need(w, Path("/usr/bin/xattr").is_file(), "system xattr unavailable")
        attrs = _bytes(["/usr/bin/xattr", str(path)], timeout=20)
        _need(w, attrs in (b"", b"com.apple.FinderInfo\n"), "unexpected Finder metadata attributes")
    elif hasattr(os, "listxattr"):
        _need(w, not os.listxattr(path), "unexpected Finder metadata attributes")
    else:
        raise w.WorkspaceError("extended-attribute inspection unsupported")


def _check_checkout(w, repo: Path, wt: Path) -> dict:
    """Review all ignored files and tracked content, including hydrated LFS.

    Plain Git `status` cannot see ignored payloads, hidden skip-worktree flags,
    or missing LFS objects. This routine is deliberately costly on large repos.
    """
    _need(w, not _git_bytes(wt, "status", "--porcelain=v1", "-z", "--untracked-files=all"),
          "dirty tracked or untracked worktree")
    flags = _git_bytes(wt, "ls-files", "-v", "-z").split(b"\0")
    _need(w, all(f.startswith(b"H ") for f in flags if f),
          "skip-worktree, assume-unchanged or unknown Git index flags")
    staged = [x for x in _git_bytes(wt, "ls-files", "--stage", "-z").split(b"\0") if x]
    entries = []
    for record in staged:
        try:
            header, path = record.split(b"\t", 1)
            mode, oid, stage = header.split(b" ")
        except ValueError as exc:
            raise w.WorkspaceError("invalid Git index record") from exc
        _need(w, mode in (b"100644", b"100755", b"120000") and stage == b"0" and
              HASH.fullmatch(oid.decode("ascii")) is not None,
              "unsupported Git index mode (submodules/sparse stage included)")
        entries.append((mode, oid.decode("ascii"), path))
    names = [x for x in _git_bytes(wt, "ls-files", "--others", "--ignored",
                                 "--exclude-standard", "--no-empty-directory", "-z").split(b"\0") if x]
    for rel in names:
        path = wt / os.fsdecode(rel)
        st = path.lstat()
        _need(w, path.name == ".DS_Store" and stat.S_ISREG(st.st_mode) and
              st.st_nlink == 1 and 8 <= st.st_size <= MAX_DS_STORE_BYTES,
              "unexpected ignored content (including non-Finder files)")
        with path.open("rb") as source:
            _need(w, source.read(8) == b"\x00\x00\x00\x01Bud1", "invalid Finder file")
        _safe_finder_xattrs(w, path)
    paths = [v[2] for v in entries]
    attrs = _git_bytes(wt, "check-attr", "--stdin", "-z", "filter",
                       input_data=b"\0".join(paths) + (b"\0" if paths else b""))
    tuples = attrs.split(b"\0")
    if tuples and tuples[-1] == b"":
        tuples.pop()
    _need(w, len(tuples) == len(paths) * 3, "Git attribute inspection incomplete")
    filters = []
    for i, path in enumerate(paths):
        _need(w, tuples[i * 3] == path and tuples[i * 3 + 1] == b"filter",
              "Git attribute output does not match exact paths")
        filt = tuples[i * 3 + 2]
        _need(w, filt in (b"unspecified", b"unset", b"lfs"),
              "unknown external filter; preserve checkout")
        filters.append(filt)
    common_raw = w.git(wt, "rev-parse", "--git-common-dir").stdout.strip()
    common = Path(common_raw) if os.path.isabs(common_raw) else wt / common_raw
    common = common.resolve()
    _need(w, common != wt.resolve() and common.is_dir(), "missing shared Git object directory")
    store = common / "lfs" / "objects"
    _need(w, not store.is_symlink() and store.resolve().is_relative_to(common),
          "Git LFS store redirected outside shared repository")
    lfs_count = 0
    lfs_bytes = 0
    deferred_lfs = 0
    for (mode, blob, rel), filt in zip(entries, filters):
        if filt != b"lfs":
            continue
        _need(w, mode in (b"100644", b"100755"), "symlink with Git LFS filter")
        pointer = _git_bytes(repo, "cat-file", "blob", blob)
        match = LFS_POINTER.fullmatch(pointer)
        _need(w, match is not None, "invalid Git LFS pointer")
        expected_sha = match.group(1).decode("ascii")
        expected_size = int(match.group(2))
        _need(w, expected_size > 0, "invalid LFS object size")
        data = wt / os.fsdecode(rel)
        object_file = store / expected_sha[:2] / expected_sha[2:4] / expected_sha
        _need(w, not object_file.is_symlink() and object_file.resolve().is_relative_to(store.resolve()),
              "LFS object is outside shared repository store")
        try:
            st = data.lstat()
            _need(w, stat.S_ISREG(st.st_mode) and st.st_nlink == 1,
                  "LFS checkout is missing or not a regular file")
            pointer_only = st.st_size == len(pointer) and data.read_bytes() == pointer
        except OSError as exc:
            raise w.WorkspaceError("LFS checkout unreadable") from exc
        if pointer_only:
            # Deferred-smudge checkouts only contain a pointer already stored in
            # Git itself. There is no hydrated payload here to be lost.
            deferred_lfs += 1
        else:
            _hash_file(w, data, expected_sha, expected_size)
            _hash_file(w, object_file, expected_sha, expected_size)
            lfs_bytes += expected_size
        lfs_count += 1
    root_dev = wt.stat().st_dev
    def walk_error(exc: OSError) -> None:
        raise w.WorkspaceError("cannot inspect complete worktree") from exc
    for current, directories, files in os.walk(wt, followlinks=False, onerror=walk_error):
        for name in directories:
            p = Path(current) / name
            st = p.lstat()
            _need(w, stat.S_ISDIR(st.st_mode) and st.st_dev == root_dev,
                  "nested mount or directory symlink")
    return {"tracked": len(entries), "ignored": len(names), "lfs_objects": lfs_count,
            "lfs_pointer_only": deferred_lfs, "verified_lfs_bytes": lfs_bytes}


def _snapshot(w, state: Path, job_id: str, repo_arg: str, retention_days: int,
              allow_legacy: bool) -> tuple[dict, Path, Path]:
    _need(w, retention_days >= DEFAULT_RETENTION_DAYS,
          "retention below 14 days is prohibited")
    _need(w, bool(job_id) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", job_id) is not None,
          "exact job ID required")
    job = w.load_job(state, job_id)
    repo = w.repo_root(Path(repo_arg))
    _need(w, Path(job.get("repo", "")).resolve() == repo, "repository identity mismatch")
    _need(w, job.get("job_id") == job_id and job.get("state") == "integrated",
          "not an integrated job")
    _need(w, not job.get("worktree_released_at"), "worktree already released")
    recorded = job.get("integrated_at", "")
    try:
        epoch = calendar.timegm(time.strptime(recorded, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError) as exc:
        raise w.WorkspaceError("invalid integration timestamp") from exc
    _need(w, epoch <= time.time() - retention_days * 86400, "still within retention period")
    cp = job.get("last_remote_checkpoint")
    branch = job.get("branch")
    commit = job.get("integration_commit")
    _need(w, all(isinstance(x, str) and HASH.fullmatch(x) is not None for x in (cp, commit))
          and cp == job.get("last_checkpoint") and branch == w.JOB_BRANCH_PREFIX + job_id,
          "checkpoint or branch identity mismatch")
    _need(w, w.repo_id(repo, job.get("remote", "origin")) == job.get("repo_id"),
          "repository identity hash mismatch")
    wt = Path(job.get("worktree", ""))
    _need(w, wt.is_absolute() and wt.name == job_id and not wt.is_symlink() and wt.is_dir(),
          "worktree missing or symlinked")
    # Existing production coordinator created worktrees in this exact parent.
    approved_parent = repo.parent / ".worktrees" / job["repo_id"]
    _need(w, not (repo.parent / ".worktrees").is_symlink() and
          not approved_parent.is_symlink() and wt.parent.resolve() == approved_parent.resolve(),
          "worktree outside coordinator-managed root or symlinked parent")
    registered = w.git(repo, "worktree", "list", "--porcelain").stdout
    blocks = [block.splitlines() for block in registered.strip().split("\n\n")]
    record = next((lines for lines in blocks if f"worktree {wt}" in lines), None)
    _need(w, record is not None and f"branch refs/heads/{branch}" in record and
          f"HEAD {cp}" in record and sum(1 for l in record if l.startswith("locked")) == 1,
          "worktree registration, lock or HEAD mismatch")
    _need(w, w.git(wt, "rev-parse", "HEAD").stdout.strip() == cp and
          w.git(repo, "rev-parse", f"refs/heads/{branch}").stdout.strip() == cp,
          "local checkpoint mismatch")
    receipt = w._receipt_file(state, job_id)
    _need(w, not receipt.is_symlink(), "symlinked integration receipt")
    if receipt.is_file():
        try:
            value = json.loads(receipt.read_text("utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise w.WorkspaceError("invalid immutable integration receipt") from exc
        _need(w, value.get("schema") == 1 and value.get("kind") == "workspace_integration_receipt" and
              all(value.get(k) == v for k, v in {"job_id": job_id, "repo": str(repo),
               "checkpoint": cp, "integration_commit": commit, "remote": job["remote"],
               "target": job["target_branch"]}.items()),
              "unexpected immutable integration receipt")
        _need(w, not value.get("legacy_unverified", False) or allow_legacy,
              "legacy receipt requires explicit operator allowance")
    else:
        _need(w, allow_legacy, "missing historical receipt; explicit legacy allowance required")
    return job, repo, wt


def _check_remote(w, state: Path, job: dict, repo: Path, allow_legacy: bool) -> None:
    _ = w._fetch_target(job)  # verify pinned remote aliases
    target_url = job["remote_url"]
    target = job["target_branch"]
    remote_branch = job["branch"]
    cp = job["last_remote_checkpoint"]
    _need(w, w._remote_ref(repo, target_url, remote_branch) == cp,
          "remote checkpoint changed")
    w.git(repo, "fetch", "--no-tags", target_url,
          f"refs/heads/{target}:refs/remotes/{job['remote']}/{target}")
    tip = w.git(repo, "rev-parse", f"refs/remotes/{job['remote']}/{target}^{{commit}}").stdout.strip()
    _need(w, w._remote_ref(repo, target_url, target) == tip,
          "canonical target moved during verification")
    integ = job["integration_commit"]
    _need(w, w.git(repo, "merge-base", "--is-ancestor", integ, tip, check=False).returncode == 0,
          "integration not in canonical GitHub history")
    message = w.git(repo, "show", "-s", "--format=%B", integ).stdout.splitlines()
    for tag, expected in (("Bridge-Job-ID", job["job_id"]),
                          ("Bridge-Resource", job["resource"]),
                          ("Bridge-Base", job["base_commit"]),
                          ("Bridge-Checkpoint", cp)):
        _need(w, f"{tag}: {expected}" in message,
              "integration commit identity mismatch")
    rev = [s.split(": ", 1)[1] for s in message if s.startswith("Bridge-Policy-Revision: ")]
    sha = [s.split(": ", 1)[1] for s in message if s.startswith("Bridge-Policy-SHA256: ")]
    receipt_file = w._receipt_file(state, job["job_id"])
    modern = bool(rev or sha)
    if modern:
        _need(w, len(rev) == len(sha) == 1 and HASH.fullmatch(rev[0]) and
              re.fullmatch(r"[0-9a-f]{64}", sha[0]) and receipt_file.is_file(),
              "modern integration unexpectedly missing or invalid receipt")
        receipt = json.loads(receipt_file.read_text("utf-8"))
        _need(w, receipt.get("policy_revision") == rev[0] and
              receipt.get("policy_sha256") == sha[0] and
              receipt.get("candidate_tree") == w.git(repo, "rev-parse", integ + "^{tree}").stdout.strip() and
              receipt.get("legacy_unverified") is False,
              "modern immutable receipt policy/tree mismatch")
    else:
        _need(w, allow_legacy or receipt_file.is_file(),
              "legacy integration requires explicit operator allowance")


def _require_operator(w) -> None:
    _need(w, sys.stdin.isatty() and sys.stdout.isatty(),
          "local interactive operator terminal required; not a mailbox command")
    _need(w, platform.system() == "Darwin", "retirement is macOS operator-only")
    _need(w, not os.environ.get("_LOCAL_EXECUTOR_NESTED_VALIDATION"), "bridge process cannot retire worktrees")
    # The known production service must not be loaded, even if currently idle.
    cmd = ["/bin/launchctl", "print", f"gui/{os.getuid()}/net.laurenzo.local-executor-bridge"]
    cp = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        check=False, timeout=15)
    _need(w, cp.returncode != 0, "production bridge LaunchAgent is loaded; stop via operator procedure")
    processes = subprocess.run(["/bin/ps", "-axo", "command="], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, check=False, timeout=20)
    _need(w, processes.returncode == 0 and not processes.stderr,
          "cannot verify bridge process quiescence")
    _need(w, not any(re.search(rb"(?:^|[\s/])bridge\.py\s+daemon(?:\s|$)", line)
                     for line in processes.stdout.splitlines()),
          "bridge daemon process still running")
    _need(w, Path("/usr/sbin/lsof").is_file(), "lsof unavailable for open-handle check")


def _check_handles(w, wt: Path) -> None:
    cp = subprocess.run(["/usr/sbin/lsof", "+D", str(wt)], stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, check=False, timeout=120)
    _need(w, cp.returncode == 1 and not cp.stdout and not cp.stderr,
          "worktree in use or lsof inspection inconclusive")


def _reconcile_removal_intent(w, args, state: Path) -> dict | None:
    """After a crash *after* removal, reconcile recorded intent only.

    This cannot begin a deletion; a missing directory with no durable intent
    remains an unresolved preservation incident rather than a success.
    """
    job = w.load_job(state, args.job)
    intent = job.get("retirement_intent")
    if not intent or job.get("worktree_released_at"):
        return None
    if (job.get("state") != "integrated" or job.get("repo") != str(w.repo_root(Path(args.repo))) or
            intent.get("job_id") != args.job or
            intent.get("checkpoint") != job.get("last_remote_checkpoint") or
            intent.get("integration_commit") != job.get("integration_commit")):
        raise w.WorkspaceError("retirement intent changed; manual recovery required")
    wt = Path(job["worktree"])
    if os.path.lexists(wt):
        return None  # Removal never completed: re-run full live checks.
    repo = w.repo_root(Path(args.repo))
    approved_parent = repo.parent / ".worktrees" / job["repo_id"]
    _need(w, wt.is_absolute() and wt.name == args.job and
          wt.parent.resolve() == approved_parent.resolve() and not approved_parent.is_symlink(),
          "missing worktree path cannot be proven to belong to coordinator")
    record = w.git(repo, "worktree", "list", "--porcelain").stdout
    _need(w, f"worktree {wt}" not in record and
          f"branch refs/heads/{job['branch']}" not in record,
          "missing worktree is still registered; manual recovery required")
    _need(w, w.git(repo, "rev-parse", f"refs/heads/{job['branch']}").stdout.strip() ==
          intent["checkpoint"], "local recovery branch changed")
    receipt = w._receipt_file(state, args.job)
    _need(w, not receipt.is_symlink() and (receipt.is_file() or args.allow_legacy),
          "historical receipt missing")
    _check_remote(w, state, job, repo, args.allow_legacy)
    job["worktree_released_at"] = w.now_iso()
    job["retirement_reconciled_at"] = w.now_iso()
    job["gc_remote_deleted"] = False
    job["retirement_audit"] = intent.get("inspection", {})
    job.pop("retirement_intent", None)
    w.save_job(state, job)
    return {"job": args.job, "retired": True, "reconciled_after_interruption": True,
            "local_branch_kept": True, "remote_branch_kept": True,
            "receipt_kept": True, "lfs_store_kept": True}


def _restore_interrupted_lock(w, args, state: Path) -> None:
    """Re-lock an exact worktree left unlocked by process termination."""
    job = w.load_job(state, args.job)
    intent = job.get("retirement_intent")
    if not intent or job.get("worktree_released_at"):
        return
    wt = Path(job.get("worktree", ""))
    if not wt.is_dir() or wt.is_symlink():
        return
    repo = w.repo_root(Path(args.repo))
    approved_parent = repo.parent / ".worktrees" / job.get("repo_id", "")
    _need(w, job.get("repo") == str(repo) and wt.name == args.job and
          wt.parent.resolve() == approved_parent.resolve() and not approved_parent.is_symlink() and
          intent.get("job_id") == args.job and
          intent.get("checkpoint") == job.get("last_remote_checkpoint") and
          intent.get("integration_commit") == job.get("integration_commit"),
          "interrupted lock recovery identity mismatch")
    registered = w.git(repo, "worktree", "list", "--porcelain").stdout
    record = next((block.splitlines() for block in registered.split("\n\n")
                   if f"worktree {wt}" in block.splitlines()), None)
    _need(w, record is not None and f"branch refs/heads/{job['branch']}" in record and
          f"HEAD {intent['checkpoint']}" in record,
          "worktree registration changed after interrupted retirement")
    if not any(line.startswith("locked") for line in record):
        _check_handles(w, wt)
        _require_operator(w)
        w.git(repo, "worktree", "lock", "--reason", f"ChatGPT workspace {args.job}", str(wt))


def _retire_locked(w, args, state: Path) -> dict:
    job, repo, wt = _snapshot(w, state, args.job, args.repo,
                              args.retention_days, args.allow_legacy)
    _check_remote(w, state, job, repo, args.allow_legacy)
    stats = _check_checkout(w, repo, wt)
    _check_handles(w, wt)
    _require_operator(w)
    # Recheck state and open handles immediately before unlock/remove.
    _check_checkout(w, repo, wt)
    _check_handles(w, wt)
    _check_remote(w, state, job, repo, args.allow_legacy)
    _require_operator(w)
    # A durable exact-job intent is written only *after* all validation.
    # If interrupted between Git removal and final metadata commit, the
    # operator can reconcile absence without resubmitting the deletion.
    job["retirement_intent"] = {"job_id": args.job,
        "checkpoint": job["last_remote_checkpoint"],
        "integration_commit": job["integration_commit"],
        "inspection": stats, "created_at": w.now_iso()}
    w.save_job(state, job)
    w.git(repo, "worktree", "unlock", str(wt))
    try:
        cp = w.git(repo, "worktree", "remove", str(wt), check=False)
        _need(w, cp.returncode == 0 and not wt.exists(),
              "worktree removal failed or has ambiguous postcondition")
    except BaseException:
        if wt.exists():
            w.git(repo, "worktree", "lock", "--reason",
                  f"ChatGPT workspace {args.job}", str(wt), check=False)
        raise
    # A historical pre-receipt job can be valid only after _check_remote has
    # independently established its exact legacy integration evidence. Do not
    # report failure *after* removing such a verified checkout merely because
    # its immutable receipt did not exist in the older coordinator protocol.
    receipt = w._receipt_file(state, job["job_id"])
    _need(w, w.git(repo, "rev-parse", f"refs/heads/{job['branch']}").stdout.strip() ==
          job["last_checkpoint"] and (receipt.is_file() or args.allow_legacy),
          "worktree removed but recovery references changed: investigate before retrying")
    job["worktree_released_at"] = w.now_iso()
    job.pop("retirement_intent", None)
    job["gc_remote_deleted"] = False
    job["retirement_audit"] = {"tracked": stats["tracked"], "lfs_objects": stats["lfs_objects"],
                               "verified_lfs_bytes": stats["verified_lfs_bytes"],
                               "lfs_pointer_only": stats["lfs_pointer_only"]}
    w.save_job(state, job)
    return {"job": args.job, "retired": True, "metadata": job["retirement_audit"],
            "local_branch_kept": True, "remote_branch_kept": True,
            "receipt_kept": True, "lfs_store_kept": True}


def gc_jobs(args, w):
    """Maintenance entry point. No broad delete; one exact audited job only."""
    if not getattr(args, "enable_maintenance_gc", False):
        raise w.WorkspaceError("destructive workspace GC is frozen; local operator must opt in")
    _need(w, bool(getattr(args, "job", None)) and bool(getattr(args, "repo", None)),
          "exact --repo and --job required; repository-wide GC is prohibited")
    _need(w, not getattr(args, "delete_remote_branch", False),
          "remote branch deletion prohibited during retirement")
    _need(w, getattr(args, "confirm_job", None) == args.job,
          "explicit --confirm-job matching exact job ID is required")
    _require_operator(w)
    state = w.state_root(args.state_dir)
    lock = w._lock(state / "workspaces" / "locks" / f"job-{args.job}.lock", nonblocking=True)
    try:
        try:
            recovered = _reconcile_removal_intent(w, args, state)
            if recovered is not None:
                return [recovered]
            _restore_interrupted_lock(w, args, state)
            return [_retire_locked(w, args, state)]
        except w.WorkspaceError:
            raise
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            raise w.WorkspaceError("retirement inspection failed; no success claimed: " + str(exc)) from exc
    finally:
        lock.close()


def lifecycle_status(args, w) -> dict:
    """Read-only inventory: no network, no unbounded traversal or state writes."""
    state = w.state_root(args.state_dir)
    records = w.list_jobs(args)
    out = {"total": 0, "by_state": {}, "integrated_retained": 0,
           "released": 0, "needs_attention": 0, "retirement_intents": 0,
           "retirement_eligible_by_age": 0, "missing_worktree": 0,
           "registered_workspace_worktrees": None, "registered_orphans": None,
           "registered_locked": None, "lfs_deferred_jobs": 0,
           "note": "Age-eligible is not preservation-verified; inspect exact job before local retirement."}
    cutoff = time.time() - DEFAULT_RETENTION_DAYS * 86400
    for job in records:
        out["total"] += 1
        if job.get("retirement_intent"):
            out["retirement_intents"] += 1
        if job.get("lfs_deferred"):
            out["lfs_deferred_jobs"] += 1
        if job.get("worktree") and not Path(job["worktree"]).is_dir() and not job.get("worktree_released_at"):
            out["missing_worktree"] += 1
        kind = job.get("state", "unknown")
        out["by_state"][kind] = out["by_state"].get(kind, 0) + 1
        if kind == "integrated" and job.get("worktree_released_at"):
            out["released"] += 1
        elif kind == "integrated":
            out["integrated_retained"] += 1
            try:
                epoch = calendar.timegm(time.strptime(job["integrated_at"], "%Y-%m-%dT%H:%M:%SZ"))
                if epoch <= cutoff:
                    out["retirement_eligible_by_age"] += 1
            except (KeyError, ValueError, TypeError):
                out["needs_attention"] += 1
        elif kind in ("interrupted", "conflicted", "unknown"):
            out["needs_attention"] += 1
    if args.repo:
        repo = w.repo_root(Path(args.repo))
        blocks = w.git(repo, "worktree", "list", "--porcelain").stdout.strip().split("\n\n")
        registered = []
        for block in blocks:
            lines = block.splitlines()
            branch = next((line.split(" ", 1)[1] for line in lines
                           if line.startswith("branch ")), "")
            if branch.startswith("refs/heads/" + w.JOB_BRANCH_PREFIX):
                registered.append((branch[len("refs/heads/" + w.JOB_BRANCH_PREFIX):],
                                   any(line.startswith("locked") for line in lines)))
        observed_ids = {job["job_id"] for job in records if job.get("job_id")}
        out["registered_workspace_worktrees"] = len(registered)
        out["registered_orphans"] = sum(jid not in observed_ids for jid, _ in registered)
        out["registered_locked"] = sum(locked for _, locked in registered)
    return out
