#!/usr/bin/env python3
from __future__ import annotations

import argparse
import calendar
import fcntl
import hashlib
import json
import os
import re
import secrets
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

JOB_BRANCH_PREFIX = "ai/workspace/"
RESOURCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}\Z")


class WorkspaceError(RuntimeError):
    pass


def run(argv, *, cwd=None, check=True, capture=True, env=None, timeout=120):
    cp = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        env=env,
        timeout=timeout,
    )
    if check and cp.returncode != 0:
        err = (cp.stderr or cp.stdout or "").strip()
        raise WorkspaceError(f"command failed rc={cp.returncode}: {' '.join(map(shlex.quote, argv))}: {err}")
    return cp


def git(repo: Path, *args, check=True, timeout=120):
    return run(["git", "-C", str(repo), *args], check=check, timeout=timeout)


def atomic_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def repo_root(path: Path) -> Path:
    cp = git(path, "rev-parse", "--show-toplevel")
    return Path(cp.stdout.strip()).resolve()


def remote_url(repo: Path, remote: str) -> str:
    return git(repo, "remote", "get-url", remote).stdout.strip()


def repo_id(repo: Path, remote: str = "origin") -> str:
    raw = f"{repo.resolve()}\0{remote_url(repo, remote)}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def state_root(cli: str | None = None) -> Path:
    if cli:
        return Path(cli).expanduser().resolve()
    return Path(os.environ.get(
        "CHATGPT_SHELL_BRIDGE_STATE_DIR",
        str(Path.home() / ".local/state/chatgpt-shell-bridge"),
    )).expanduser().resolve()


def job_path(state: Path, job_id: str) -> Path:
    return state / "workspaces" / "jobs" / f"{job_id}.json"


def load_job(state: Path, job_id: str) -> dict:
    p = job_path(state, job_id)
    if not p.is_file():
        raise WorkspaceError(f"unknown workspace job: {job_id}")
    v = json.loads(p.read_text("utf-8"))
    if not isinstance(v, dict):
        raise WorkspaceError("invalid job metadata")
    return v


def save_job(state: Path, job: dict) -> None:
    job["updated_at"] = now_iso()
    atomic_json(job_path(state, job["job_id"]), job)


def _safe_resource(value: str) -> str:
    if not RESOURCE_RE.fullmatch(value):
        raise WorkspaceError("resource must be 1..200 characters using letters/digits/._:/-")
    return value


def _new_job_id(resource: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", resource.lower()).strip("-")[-36:] or "job"
    return f"ws-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{slug}-{secrets.token_hex(3)}"


def _lock(path: Path, nonblocking=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open("a+")
    flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
    try:
        fcntl.flock(f.fileno(), flags)
    except BlockingIOError:
        f.close(); raise WorkspaceError(f"resource is busy: {path.name}")
    return f


def _remote_ref(repo: Path, remote: str, branch: str) -> str:
    cp = git(repo, "ls-remote", remote, f"refs/heads/{branch}")
    lines = [x for x in cp.stdout.splitlines() if x.strip()]
    if len(lines) != 1:
        raise WorkspaceError(f"remote branch not found or ambiguous: {remote}/{branch}")
    return lines[0].split()[0]


def _verify_remote_branch(repo: Path, remote: str, branch: str, expected: str) -> None:
    actual = _remote_ref(repo, remote, branch)
    if actual != expected:
        raise WorkspaceError(f"remote verification failed for {remote}/{branch}: expected {expected}, got {actual}")


def create_job(args) -> dict:
    state = state_root(args.state_dir)
    repo = repo_root(Path(args.repo))
    resource = _safe_resource(args.resource)
    remote = args.remote
    target = args.target
    git(repo, "fetch", "--prune", remote, f"refs/heads/{target}:refs/remotes/{remote}/{target}")
    base = git(repo, "rev-parse", f"refs/remotes/{remote}/{target}^{{commit}}").stdout.strip()
    jid = args.job_id or _new_job_id(resource)
    if job_path(state, jid).exists():
        raise WorkspaceError(f"job already exists: {jid}")
    branch = f"{JOB_BRANCH_PREFIX}{jid}"
    exists = git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
    if exists.returncode == 0:
        raise WorkspaceError(f"job branch already exists: {branch}")
    rid = repo_id(repo, remote)
    worktree_root = Path(args.worktree_root).expanduser().resolve() if args.worktree_root else repo.parent / ".worktrees" / rid
    worktree = worktree_root / jid
    if worktree.exists():
        raise WorkspaceError(f"worktree path already exists: {worktree}")
    worktree.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "--lock", "--reason", f"ChatGPT workspace {jid}", "-b", branch, str(worktree), base)
    created = now_iso()
    job = {
        "schema": 1,
        "job_id": jid,
        "repo": str(repo),
        "repo_id": rid,
        "remote": remote,
        "remote_url": remote_url(repo, remote),
        "target_branch": target,
        "base_commit": base,
        "branch": branch,
        "worktree": str(worktree),
        "resource": resource,
        "state": "active",
        "created_at": created,
        "updated_at": created,
        "last_checkpoint": base,
        "last_remote_checkpoint": None,
        "reconciled_target": base,
        "integration_commit": None,
    }
    save_job(state, job)
    if args.push_initial:
        git(worktree, "push", "-u", remote, f"HEAD:refs/heads/{branch}")
        head = git(worktree, "rev-parse", "HEAD").stdout.strip()
        _verify_remote_branch(repo, remote, branch, head)
        job["last_remote_checkpoint"] = head
        save_job(state, job)
    return job


def _dirty(worktree: Path) -> bool:
    return bool(git(worktree, "status", "--porcelain").stdout)


def _checkpoint(state: Path, job: dict, message: str | None = None) -> str:
    wt = Path(job["worktree"])
    git(wt, "add", "-A")
    staged = git(wt, "diff", "--cached", "--quiet", check=False)
    if staged.returncode == 0:
        head = git(wt, "rev-parse", "HEAD").stdout.strip()
    else:
        msg = message or f"checkpoint: {job['job_id']}"
        git(wt, "commit", "--no-verify", "-m", msg)
        head = git(wt, "rev-parse", "HEAD").stdout.strip()
    git(wt, "push", job["remote"], f"HEAD:refs/heads/{job['branch']}")
    _verify_remote_branch(Path(job["repo"]), job["remote"], job["branch"], head)
    job["last_checkpoint"] = head
    job["last_remote_checkpoint"] = head
    job["state"] = "active"
    save_job(state, job)
    return head


def _descendant_pids(root_pid: int) -> list[int]:
    cp = subprocess.run(["ps", "-axo", "pid=,ppid="], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False)
    children: dict[int, list[int]] = {}
    for line in cp.stdout.splitlines():
        try:
            pid_s, ppid_s = line.split()[:2]; pid=int(pid_s); ppid=int(ppid_s)
        except Exception:
            continue
        children.setdefault(ppid, []).append(pid)
    out=[]; stack=list(children.get(root_pid, []))
    while stack:
        pid=stack.pop(); out.append(pid); stack.extend(children.get(pid, []))
    return out


def _terminate_tree(proc: subprocess.Popen) -> None:
    pids=list(reversed(_descendant_pids(proc.pid))) + [proc.pid]
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            try: os.kill(pid, sig)
            except ProcessLookupError: pass
            except PermissionError: pass
        try:
            proc.wait(timeout=0.5)
            return
        except subprocess.TimeoutExpired:
            continue


def _run_job_shell(shell: str, command: str, cwd: Path, timeout: int):
    # Deliberately inherit the outer shell-bridge process group. If the outer
    # request is contained, the entire workspace command tree is contained too.
    proc=subprocess.Popen([shell,"-lc",command],cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=os.environ.copy())
    try:
        stdout,stderr=proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_tree(proc)
        stdout,stderr=proc.communicate()
        raise WorkspaceError(f"command timed out after {timeout}s")
    return proc.returncode,stdout,stderr


def exec_job(args) -> dict:
    state = state_root(args.state_dir)
    job = load_job(state, args.job)
    if job.get("state") == "integrated":
        raise WorkspaceError("job is already integrated")
    wt = Path(job["worktree"])
    if not wt.is_dir():
        raise WorkspaceError("job worktree is missing; run recover")
    lock = _lock(state / "workspaces" / "locks" / f"job-{job['job_id']}.lock")
    try:
        started = time.time()
        rc, stdout, stderr = _run_job_shell(args.shell, args.command, wt, args.timeout)
        out = {
            "job_id": job["job_id"], "exit_code": rc,
            "stdout": stdout, "stderr": stderr,
            "duration_seconds": round(time.time()-started, 6),
            "checkpoint": None,
        }
        if rc == 0 and not args.no_checkpoint and _dirty(wt):
            out["checkpoint"] = _checkpoint(state, job, args.checkpoint_message)
        elif rc != 0:
            job["state"] = "interrupted"
            job["last_error"] = f"command exited {rc}"
            save_job(state, job)
        return out
    except WorkspaceError as exc:
        job["state"] = "interrupted"
        job["last_error"] = str(exc)
        save_job(state, job)
        raise
    finally:
        lock.close()


def list_jobs(args) -> list[dict]:
    state = state_root(args.state_dir)
    d = state / "workspaces" / "jobs"
    out=[]
    if d.is_dir():
        for p in sorted(d.glob("*.json")):
            try:
                j=json.loads(p.read_text("utf-8"))
            except Exception:
                continue
            if args.repo and Path(j.get("repo", "")).resolve() != repo_root(Path(args.repo)):
                continue
            out.append(j)
    return out


def show_job(args) -> dict:
    return load_job(state_root(args.state_dir), args.job)


def ready_job(args) -> dict:
    state=state_root(args.state_dir); job=load_job(state,args.job); wt=Path(job["worktree"])
    if _dirty(wt):
        if args.checkpoint:
            _checkpoint(state, job, args.message)
        else:
            raise WorkspaceError("job worktree is dirty; checkpoint before marking ready")
    head=git(wt,"rev-parse","HEAD").stdout.strip()
    _verify_remote_branch(Path(job["repo"]),job["remote"],job["branch"],head)
    job["last_checkpoint"]=head; job["last_remote_checkpoint"]=head; job["state"]="ready"
    save_job(state,job); return job


def mark_reconciled(args) -> dict:
    state=state_root(args.state_dir); job=load_job(state,args.job); repo=Path(job["repo"])
    git(repo,"fetch","--prune",job["remote"],f"refs/heads/{job['target_branch']}:refs/remotes/{job['remote']}/{job['target_branch']}")
    target=git(repo,"rev-parse",f"refs/remotes/{job['remote']}/{job['target_branch']}^{{commit}}").stdout.strip()
    if args.target and args.target != target:
        raise WorkspaceError(f"target moved: expected {args.target}, actual {target}")
    wt = Path(job["worktree"])
    if _dirty(wt):
        raise WorkspaceError("job worktree is dirty; checkpoint before reconciliation")
    checkpoint = git(wt, "rev-parse", "HEAD").stdout.strip()
    if git(repo, "merge-base", "--is-ancestor", target, checkpoint, check=False).returncode != 0:
        raise WorkspaceError("reconciliation not proven: canonical target is not an ancestor of the job checkpoint")
    job["reconciled_target"]=target; save_job(state,job); return job


def _changed_paths(repo: Path, a: str, b: str) -> set[str]:
    cp=git(repo,"diff","--name-only",f"{a}..{b}")
    return {x for x in cp.stdout.splitlines() if x}


def _resource_seen_since(repo: Path, base: str, current: str, resource: str) -> bool:
    if base == current: return False
    cp=git(repo,"log","--format=%B%x00",f"{base}..{current}")
    needle=f"Bridge-Resource: {resource}"
    return any(needle in msg for msg in cp.stdout.split("\x00"))


def integrate_job(args) -> dict:
    state=state_root(args.state_dir); job=load_job(state,args.job); repo=Path(job["repo"]); wt=Path(job["worktree"])
    if job.get("state") not in {"ready","active","conflicted","interrupted"}:
        raise WorkspaceError(f"job state cannot integrate: {job.get('state')}")
    if _dirty(wt): raise WorkspaceError("job worktree is dirty; checkpoint first")
    checkpoint=git(wt,"rev-parse","HEAD").stdout.strip()
    _verify_remote_branch(repo,job["remote"],job["branch"],checkpoint)
    lock_name=hashlib.sha256(f"{job['repo_id']}\0{job['target_branch']}".encode()).hexdigest()[:24]
    lock=_lock(state/"workspaces"/"locks"/f"integrate-{lock_name}.lock")
    tmp=None
    try:
        git(repo,"fetch","--prune",job["remote"],f"refs/heads/{job['target_branch']}:refs/remotes/{job['remote']}/{job['target_branch']}")
        current=git(repo,"rev-parse",f"refs/remotes/{job['remote']}/{job['target_branch']}^{{commit}}").stdout.strip()
        anc=git(repo,"merge-base","--is-ancestor",job["base_commit"],current,check=False)
        if anc.returncode != 0:
            raise WorkspaceError("job base is no longer an ancestor of canonical target; manual recovery required")
        changed_job=_changed_paths(repo,job["base_commit"],checkpoint)
        changed_main=_changed_paths(repo,job["base_commit"],current)
        overlap=sorted(changed_job & changed_main)
        resource_seen=_resource_seen_since(repo,job["base_commit"],current,job["resource"])
        reconciled=job.get("reconciled_target") == current
        if reconciled and git(repo, "merge-base", "--is-ancestor", current, checkpoint, check=False).returncode != 0:
            reconciled = False
        if (overlap or resource_seen) and not reconciled:
            job["state"]="conflicted"; job["conflict_target"]=current; job["overlap_paths"]=overlap; job["resource_overlap"]=resource_seen; save_job(state,job)
            raise WorkspaceError("reconcile_required: canonical target changed in the same logical resource or paths")
        integ_root=repo.parent/".worktrees"/job["repo_id"]/".integration"
        integ_root.mkdir(parents=True,exist_ok=True)
        tmp=integ_root/f"{job['job_id']}-{secrets.token_hex(3)}"
        git(repo,"worktree","add","--detach",str(tmp),current)
        merge=git(tmp,"merge","--squash",checkpoint,check=False,timeout=args.timeout)
        if merge.returncode != 0:
            job["state"]="conflicted"; job["last_error"]=(merge.stderr or merge.stdout).strip(); save_job(state,job)
            raise WorkspaceError("squash merge conflicted; job requires reconciliation")
        if args.validate:
            cp=subprocess.run([args.shell,"-lc",args.validate],cwd=tmp,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=args.timeout)
            if cp.returncode != 0:
                raise WorkspaceError(f"integration validation failed rc={cp.returncode}: {(cp.stderr or cp.stdout)[-3000:]}")
        if git(tmp,"diff","--cached","--quiet",check=False).returncode == 0:
            raise WorkspaceError("job has no canonical changes to integrate")
        msg=args.message or f"Integrate workspace {job['job_id']}"
        body=(f"{msg}\n\nBridge-Job-ID: {job['job_id']}\nBridge-Resource: {job['resource']}\n"
              f"Bridge-Base: {job['base_commit']}\nBridge-Checkpoint: {checkpoint}\n")
        git(tmp,"commit","-m",body,timeout=args.timeout)
        candidate=git(tmp,"rev-parse","HEAD").stdout.strip()
        git(repo,"fetch","--prune",job["remote"],f"refs/heads/{job['target_branch']}:refs/remotes/{job['remote']}/{job['target_branch']}")
        latest=git(repo,"rev-parse",f"refs/remotes/{job['remote']}/{job['target_branch']}^{{commit}}").stdout.strip()
        if latest != current:
            raise WorkspaceError(f"canonical target moved during integration: {current} -> {latest}")
        push=git(tmp,"push",job["remote"],f"HEAD:refs/heads/{job['target_branch']}",check=False,timeout=args.timeout)
        if push.returncode != 0:
            raise WorkspaceError(f"canonical push rejected: {(push.stderr or push.stdout).strip()}")
        _verify_remote_branch(repo,job["remote"],job["target_branch"],candidate)
        job["state"]="integrated"; job["integration_commit"]=candidate; job["integrated_at"]=now_iso(); job["last_checkpoint"]=checkpoint; job["last_remote_checkpoint"]=checkpoint
        save_job(state,job)
        return job
    finally:
        if tmp is not None and tmp.exists():
            git(repo,"worktree","remove","--force",str(tmp),check=False)
        lock.close()


def recover_jobs(args) -> list[dict]:
    state=state_root(args.state_dir); repo=repo_root(Path(args.repo)); remote=args.remote; target=args.target
    rid=repo_id(repo,remote)
    cp=git(repo,"worktree","list","--porcelain")
    blocks=cp.stdout.strip().split("\n\n") if cp.stdout.strip() else []
    recovered=[]
    for block in blocks:
        data={}
        for line in block.splitlines():
            if " " in line:
                k,v=line.split(" ",1); data[k]=v
        branch=data.get("branch","")
        prefix="refs/heads/"+JOB_BRANCH_PREFIX
        if not branch.startswith(prefix): continue
        jid=branch[len(prefix):]
        p=job_path(state,jid)
        if p.exists():
            recovered.append(load_job(state,jid)); continue
        wt=Path(data["worktree"]); head=data.get("HEAD") or git(wt,"rev-parse","HEAD").stdout.strip()
        git(repo,"fetch","--prune",remote,f"refs/heads/{target}:refs/remotes/{remote}/{target}")
        current=git(repo,"rev-parse",f"refs/remotes/{remote}/{target}^{{commit}}").stdout.strip()
        mb=git(repo,"merge-base",head,current).stdout.strip()
        job={"schema":1,"job_id":jid,"repo":str(repo),"repo_id":rid,"remote":remote,"remote_url":remote_url(repo,remote),"target_branch":target,"base_commit":mb,"branch":JOB_BRANCH_PREFIX+jid,"worktree":str(wt),"resource":f"repo:{rid}","state":"interrupted","created_at":now_iso(),"updated_at":now_iso(),"last_checkpoint":head,"last_remote_checkpoint":None,"reconciled_target":mb,"integration_commit":None,"recovery_note":"metadata reconstructed conservatively from Git worktree"}
        save_job(state,job); recovered.append(job)
    return recovered


def gc_jobs(args) -> list[str]:
    state=state_root(args.state_dir); removed=[]; cutoff=time.time()-args.retention_days*86400
    for job in list_jobs(argparse.Namespace(state_dir=args.state_dir,repo=args.repo)):
        if job.get("state") != "integrated": continue
        try: integrated_epoch=calendar.timegm(time.strptime(job["integrated_at"],"%Y-%m-%dT%H:%M:%SZ"))
        except Exception: continue
        if integrated_epoch > cutoff: continue
        repo=Path(job["repo"]); wt=Path(job["worktree"]); commit=job.get("integration_commit")
        git(repo,"fetch","--prune",job["remote"],f"refs/heads/{job['target_branch']}:refs/remotes/{job['remote']}/{job['target_branch']}")
        current=git(repo,"rev-parse",f"refs/remotes/{job['remote']}/{job['target_branch']}^{{commit}}").stdout.strip()
        if git(repo,"merge-base","--is-ancestor",commit,current,check=False).returncode != 0:
            continue
        if wt.exists() and _dirty(wt): continue
        if wt.exists(): git(repo,"worktree","remove",str(wt),check=False)
        git(repo,"branch","-D",job["branch"],check=False)
        if args.delete_remote_branch:
            git(repo,"push",job["remote"],f":refs/heads/{job['branch']}",check=False)
        job_path(state,job["job_id"]).unlink(missing_ok=True); removed.append(job["job_id"])
    return removed


def emit(value):
    print(json.dumps(value,indent=2,sort_keys=True))


def main():
    ap=argparse.ArgumentParser(description="Durable concurrent Git workspaces for ChatGPT Shell Bridge")
    ap.add_argument("--state-dir")
    sub=ap.add_subparsers(dest="cmd",required=True)
    p=sub.add_parser("create"); p.add_argument("--repo",required=True); p.add_argument("--resource",required=True); p.add_argument("--remote",default="origin"); p.add_argument("--target",default="main"); p.add_argument("--job-id"); p.add_argument("--worktree-root"); p.add_argument("--push-initial",action="store_true"); p.set_defaults(fn=create_job)
    p=sub.add_parser("exec"); p.add_argument("--job",required=True); p.add_argument("--command",required=True); p.add_argument("--timeout",type=int,default=120); p.add_argument("--shell",default="/bin/zsh" if Path('/bin/zsh').exists() else "/bin/bash"); p.add_argument("--no-checkpoint",action="store_true"); p.add_argument("--checkpoint-message"); p.set_defaults(fn=exec_job)
    p=sub.add_parser("list"); p.add_argument("--repo"); p.set_defaults(fn=list_jobs)
    p=sub.add_parser("show"); p.add_argument("--job",required=True); p.set_defaults(fn=show_job)
    p=sub.add_parser("ready"); p.add_argument("--job",required=True); p.add_argument("--checkpoint",action="store_true"); p.add_argument("--message"); p.set_defaults(fn=ready_job)
    p=sub.add_parser("mark-reconciled"); p.add_argument("--job",required=True); p.add_argument("--target"); p.set_defaults(fn=mark_reconciled)
    p=sub.add_parser("integrate"); p.add_argument("--job",required=True); p.add_argument("--message"); p.add_argument("--validate"); p.add_argument("--timeout",type=int,default=240); p.add_argument("--shell",default="/bin/zsh" if Path('/bin/zsh').exists() else "/bin/bash"); p.set_defaults(fn=integrate_job)
    p=sub.add_parser("recover"); p.add_argument("--repo",required=True); p.add_argument("--remote",default="origin"); p.add_argument("--target",default="main"); p.set_defaults(fn=recover_jobs)
    p=sub.add_parser("gc"); p.add_argument("--repo"); p.add_argument("--retention-days",type=int,default=14); p.add_argument("--delete-remote-branch",action="store_true"); p.set_defaults(fn=gc_jobs)
    args=ap.parse_args()
    try:
        emit(args.fn(args)); return 0
    except WorkspaceError as exc:
        print(json.dumps({"ok":False,"error":str(exc)},sort_keys=True),file=sys.stderr); return 2


if __name__=="__main__":
    raise SystemExit(main())
