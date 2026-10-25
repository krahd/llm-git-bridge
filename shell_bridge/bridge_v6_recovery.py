#!/usr/bin/env python3
"""Mac-local bridge v6-first containment. Do not run through a bridge mailbox.

Default mode is read-only. The narrowly scoped --contain operation keeps the
legacy v5 service online until a fresh v6 build is separately qualified.
Never deletes request journals, approvals, configuration, or Git content.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import stat
import os
from pathlib import Path
import plistlib
import re
import shlex
import subprocess
import sys
import time

KEEP = "com.tom.chatgpt-shell-bridge"
PRODUCTION = "1PRsQHgVsgXhIYT_8akGFRGdhdlw9Lmk6"
STAGING = "15ql2yACOq7H6qo0IgzosySqW8nHgUKXv"
REQUIRED = ("bridge_instance_id", "drive_root_folder_id", "requests_folder_id",
            "results_folder_id", "state_dir")
LABEL = re.compile(r"(?:com\.tom\.chatgpt-shell-bridge|io\.llm-git-bridge\.daemon|net\.laurenzo\.local-executor-bridge(?:[-.][A-Za-z0-9._-]+)?)\Z")
BRIDGE_ARG = re.compile(r"(?:^|/)bridge\.py\s+daemon(?:\s|$)")
FINGERPRINT_SECONDS = 600

class Hold(RuntimeError):
    pass

def command(argv: list[str], *, max_seconds: int = 12) -> str:
    cp = subprocess.run(argv, capture_output=True, text=True, timeout=max_seconds)
    if cp.returncode:
        raise Hold(f"local command failed: {argv[0]} ({cp.returncode})")
    return cp.stdout

def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()

def safe_path(path: Path, root: Path, *, regular: bool = False) -> None:
    """Reject lexical escapes, symlinked ancestors, and non-owned regular files."""
    if not path.is_absolute() or not root.is_absolute() or not path.is_relative_to(root):
        raise Hold("path escapes expected root")
    cursor = path
    while cursor != root.parent:
        if cursor.is_symlink():
            raise Hold("symlinked path or ancestor")
        cursor = cursor.parent
    if regular:
        if not path.is_file():
            raise Hold("missing regular file")
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or (info.st_mode & 0o022):
            raise Hold("unsafe file owner or permissions")


def safe_file(path: Path, home: Path) -> bytes:
    safe_path(path, home, regular=True)
    if path.stat().st_size > 65536:
        raise Hold("oversized configuration/registration")
    return path.read_bytes()


def parse_service(plist: Path, home: Path) -> dict:
    name = plist.stem
    if not LABEL.fullmatch(name) or plist.name != name + ".plist":
        raise Hold("unrecognised bridge plist")
    raw = safe_file(plist, home)
    obj = plistlib.loads(raw)
    if obj.get("Label") != name:
        raise Hold(f"plist label differs: {name}")
    args = obj.get("ProgramArguments")
    if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
        raise Hold(f"invalid program arguments: {name}")
    scripts = [i for i,x in enumerate(args) if x.endswith("/bridge.py")]
    if len(scripts) != 1 or scripts[0]+1 >= len(args) or args[scripts[0]+1] != "daemon":
        raise Hold(f"not an identified bridge daemon: {name}")
    confs = [args[i+1] for i,x in enumerate(args[:-1]) if x == "--config"]
    confs += [x.partition("=")[2] for x in args if x.startswith("--config=")]
    if len(confs) != 1:
        raise Hold(f"ambiguous config: {name}")
    script, config = Path(args[scripts[0]]), Path(confs[0])
    safe_path(script, home / ".local/share", regular=True)
    safe_path(config, home / ".config", regular=True)
    raw_conf = safe_file(config, home)
    cfg = json.loads(raw_conf)
    if not isinstance(cfg, dict) or not all(isinstance(cfg.get(x), str) and cfg[x] for x in REQUIRED):
        raise Hold(f"invalid required config fields: {name}")
    if cfg["drive_root_folder_id"] not in (PRODUCTION, STAGING):
        raise Hold(f"unknown mailbox: {name}")
    remote=cfg.get("remote")
    if not isinstance(remote,str) or not re.fullmatch(r"[A-Za-z0-9_-]+:",remote):
        raise Hold(f"missing/unrecognised rclone remote: {name}")
    state = Path(cfg["state_dir"]).expanduser()
    safe_path(state, home / ".local/state")
    if not state.is_dir():
        raise Hold(f"missing journal location: {name}")
    return {"label":name,"plist":str(plist),"pid":None,"loaded":False,
            "script":str(script),"config":str(config),"state":str(state),
            "mailbox":cfg["drive_root_folder_id"],
            "requests":cfg["requests_folder_id"],"results":cfg["results_folder_id"],
            "registration_sha256":digest(raw),"config_sha256":digest(raw_conf),
            "script_sha256":digest(script.read_bytes()), "remote":remote}

def ps_table(raw: str) -> dict[int, tuple[int,int,str]]:
    out = {}
    for line in raw.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) == 4 and all(x.isdigit() for x in parts[:3]):
            out[int(parts[0])] = (int(parts[1]), int(parts[2]), parts[3])
    return out

def active_descendants(pid: int, processes: dict[int,tuple[int,int,str]]) -> list[int]:
    found = set()
    while True:
        nxt = {p for p,(ppid,pgid,_) in processes.items()
               if p != pid and (ppid == pid or ppid in found or pgid == pid)}
        if nxt <= found:
            return sorted(found)
        found |= nxt

def approval_summary(state: Path, now: float) -> dict:
    root = state / "approvals"
    pending = root / "pending"
    data = {"total":0,"expired":0,"unexpired":0,"unverifiable":0}
    if pending.is_symlink() or root.is_symlink():
        raise Hold(f"linked approval directory: {state.name}")
    if not pending.exists():
        return data
    if not pending.is_dir():
        raise Hold(f"linked/broken approval directory: {state.name}")
    paths = list(pending.iterdir())
    if len(paths) > 4096:
        raise Hold(f"approval listing exceeds cap: {state.name}")
    for path in paths:
        data["total"] += 1
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            data["unverifiable"] += 1
            continue
        try:
            if path.stat().st_size > 65536:
                raise ValueError("oversized")
            record = json.loads(path.read_text())
            if (record.get("kind") != "operator_approval_request" or
                    record.get("request_id") != path.stem or
                    record.get("protocol") != 1):
                raise ValueError("invalid request")
            deadline = float(record["expires_at"])
            if not math.isfinite(deadline) or deadline <= 0:
                raise ValueError("invalid expiry")
            if deadline <= now:
                data["expired"] += 1
            else:
                data["unexpired"] += 1
        except (ValueError, TypeError, OSError, KeyError):
            data["unverifiable"] += 1
    return data

def journal_activity(state: Path) -> dict:
    out = {"scanned":0,"active_markers":0,"started_unfinished":0}
    root = state / "requests"
    safe_path(root, state)
    if not root.is_dir():
        raise Hold(f"missing/linked journal: {state.name}")
    # Count evidence without opening arbitrary request content.
    for entry in root.iterdir():
        out["scanned"] += 1
        if out["scanned"] > 100000 or entry.is_symlink() or not entry.is_dir():
            raise Hold(f"unexpected journal entry/budget: {state.name}")
        for marker in ("active.json", "started.json", "finished.json", "result.json"):
            if (entry / marker).is_symlink():
                raise Hold(f"linked journal receipt marker: {state.name}")
        if (entry / "active.json").exists():
            out["active_markers"] += 1
        if (entry / "started.json").exists() and not (entry / "finished.json").exists():
            out["started_unfinished"] += 1
    return out

def remote_request_queue(remote: str, folder_id: str) -> list[str]:
    """List by the immutable Drive requests-folder ID, never by folder name."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+:", remote):
        raise Hold("unsafe rclone remote")
    if not re.fullmatch(r"[A-Za-z0-9_-]{12,128}", folder_id):
        raise Hold("unsafe pinned Drive folder ID")
    raw = command(["rclone", "--drive-root-folder-id", folder_id, "--timeout", "20s",
                   "lsf", remote, "--files-only", "--max-depth", "1"],max_seconds=30)
    if len(raw.encode()) > 1024*1024:
        raise Hold("remote listing exceeds bounded output")
    names=raw.splitlines()
    if len(names)>4096 or any(not name or "/" in name or "\x00" in name for name in names):
        raise Hold("remote listing is incomplete or malformed")
    # The bridge processes raw *.json request files; legacy *.disabled and
    # historic *.txt artifacts do not execute. Preserve them untouched.
    return sorted(name for name in names if name.endswith(".json"))


def verify_operator_context() -> None:
    """Prohibit bridge-child service management, even if run on macOS."""
    processes = ps_table(command(["/bin/ps", "-axo", "pid=,ppid=,pgid=,command="]))
    ancestor=os.getpid()
    for _ in range(32):
        if ancestor == 1:
            return
        if ancestor not in processes:
            raise Hold("cannot verify operator process ancestry")
        parent,_,cmdline=processes[ancestor]
        if BRIDGE_ARG.search(cmdline):
            raise Hold("refuse bridge-child service management")
        if parent <= 0 or parent == ancestor:
            raise Hold("invalid operator process ancestry")
        ancestor=parent
    raise Hold("operator ancestry exceeds limit")


def inventory(home: Path, *, now: float | None = None, launch: str | None = None,
              ps: str | None = None, remote_listings: dict | None = None) -> dict:
    now = time.time() if now is None else now
    launch = command(["/bin/launchctl","list"]) if launch is None else launch
    ps = command(["/bin/ps","-axo","pid=,ppid=,pgid=,command="]) if ps is None else ps
    agents = home / "Library/LaunchAgents"
    safe_path(agents, home)
    all_plists = sorted(p for p in agents.glob("*.plist") if LABEL.fullmatch(p.stem))
    if not all_plists or len(all_plists) > 64:
        raise Hold("no/too many bridge LaunchAgent plists")
    services = {p.stem:parse_service(p,home) for p in all_plists}
    loaded = {}
    for line in launch.splitlines():
        fields = line.split(None,2)
        if len(fields)==3 and LABEL.fullmatch(fields[2]):
            loaded[fields[2]]=int(fields[0]) if fields[0].isdigit() else None
            if fields[2] not in services:
                raise Hold("loaded service has no inspectable LaunchAgent registration")
    proc = ps_table(ps)
    bridge_pids = set()
    for label,pid in loaded.items():
        s=services[label]
        s["loaded"]=True
        s["pid"]=pid
        if pid is None:
            continue
        if pid not in proc:
            raise Hold(f"missing launchd process: {label}")
        argv = shlex.split(proc[pid][2])
        if s["script"] not in argv or s["config"] not in argv or "daemon" not in argv:
            raise Hold(f"daemon process/config mismatch: {label}")
        s["children"] = active_descendants(pid,proc)
        bridge_pids.add(pid)
    # Refuse to bypass unregistered bridge.py daemon processes.
    for pid,(_,_,cmdline) in proc.items():
        if BRIDGE_ARG.search(cmdline) and pid not in bridge_pids:
            raise Hold(f"unregistered bridge daemon process: {pid}")
    if KEEP not in loaded or loaded[KEEP] is None:
        raise Hold("v5 temporary production owner not running")
    if services[KEEP]["mailbox"] != PRODUCTION:
        raise Hold("v5 service does not own expected production mailbox")
    states = {}
    queues = {}
    for s in services.values():
        st = s["state"]
        if st not in states:
            states[st] = {"approvals":approval_summary(Path(st),now),
                          "activity":journal_activity(Path(st))}
        ident=(s["remote"],s["requests"])
        if ident not in queues:
            if remote_listings is None:
                queues[ident]=remote_request_queue(*ident)
            else:
                if ident not in remote_listings:
                    raise Hold("missing independent remote queue snapshot")
                queues[ident]=remote_listings[ident]
    targets = sorted(x for x in services if x != KEEP)
    reasons = []
    if not targets:
        reasons.append("no targets to contain")
    for label in sorted(services):
        s=services[label]
        if s.get("children"):
            reasons.append(f"live child process(es) of {label}")
    for state_path, state in states.items():
        if state["approvals"]["unexpired"] or state["approvals"]["unverifiable"]:
            reasons.append(f"actionable or unverifiable approvals in {state_path}")
        if state["activity"]["active_markers"] or state["activity"]["started_unfinished"]:
            reasons.append(f"active/incomplete journal state in {state_path}")
    for (remote,folder_id),entries in queues.items():
        if entries:
            reasons.append(f"executable remote requests queued in {folder_id}")
    # Include both mailbox observations and their pinned identities in the
    # operator's fingerprint. Never trust a stale, unrelated Drive listing.
    queue_evidence=[{"remote":a,"folder_id":b,"pending":queues[(a,b)]}
                    for a,b in sorted(queues)]
    evidence = {"services":services,"states":states,"targets":targets,
                "remote_queues":queue_evidence,
                "time_window":int(now // FINGERPRINT_SECONDS)}
    token = digest(json.dumps(evidence,sort_keys=True,separators=(",",":")).encode())
    return {"schema":2,"mode":"read_only","keeper":KEEP,"targets":targets,
            "services":services,"states":states,"remote_queues":queue_evidence,
            "holds":sorted(set(reasons)),
            "containment_ready":not reasons,"fingerprint":token}

def containment(report: dict, home: Path, expected: str) -> None:
    if not report["containment_ready"] or report["fingerprint"] != expected:
        raise Hold("read-only preflight failed or fingerprint changed")
    verify_operator_context()
    archive = home / "Library/Application Support/llm-git-bridge/retired-launchagents"
    safe_path(archive, home)
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = archive / run_id
    if dest.exists():
        raise Hold("archive name collision")
    # Recheck process and byte identity immediately before applying any changes.
    fresh = inventory(home)
    if fresh["fingerprint"] != expected or not fresh["containment_ready"]:
        raise Hold("environment changed after operator review")
    if archive.exists():
        safe_path(archive, home)
        if archive.stat().st_uid != os.getuid() or archive.stat().st_mode & 0o077:
            raise Hold("unsafe archive permissions")
    dest.mkdir(parents=True,mode=0o700)
    safe_path(dest, home)
    report_path = dest / "containment.jsonl"
    def log(event:dict) -> None:
        with report_path.open("a",encoding="utf-8") as f:
            f.write(json.dumps(event,sort_keys=True)+"\n")
            f.flush(); os.fsync(f.fileno())
    log({"event":"begin","fingerprint":expected,"keeper":KEEP,"targets":fresh["targets"]})
    # No rollback/re-enable from a partially applied operation: inspect first.
    for label in fresh["targets"]:
        s=fresh["services"][label]
        source = Path(s["plist"])
        target = dest / source.name
        safe_path(source, home, regular=True)
        if (digest(source.read_bytes())!=s["registration_sha256"] or
                digest(Path(s["config"]).read_bytes()) != s["config_sha256"] or
                digest(Path(s["script"]).read_bytes()) != s["script_sha256"]):
            raise Hold("registration, config or executable changed during containment")
        if target.exists() or target.is_symlink():
            raise Hold("existing archive target must not be overwritten")
        # Recheck live work before each irreversible service operation. The
        # original snapshot must still describe the installation identity;
        # previous targets have already been removed, so cannot reuse its hash.
        latest = inventory(home)
        if (not latest["containment_ready"] or label not in latest["targets"] or
                latest["services"][label]["registration_sha256"] != s["registration_sha256"]):
            raise Hold("live preflight changed before service modification")
        log({"event":"start","label":label,"pid":s["pid"]})
        unit=f"gui/{os.getuid()}/{label}"
        command(["/bin/launchctl","disable",unit])
        log({"event":"disabled","label":label})
        if s["loaded"]:
            command(["/bin/launchctl","bootout",unit])
            log({"event":"bootout","label":label})
        # OS process termination is not synchronous with launchctl acknowledgment.
        for _ in range(12):
            current = command(["/bin/launchctl","list"])
            if not any(line.split(None,2)[-1]==label for line in current.splitlines() if len(line.split(None,2))==3):
                break
            time.sleep(.25)
        else:
            raise Hold("launchd job remains loaded after bootout; manual hold")
        if s["pid"] is not None:
            current_pids = ps_table(command(["/bin/ps","-axo","pid=,ppid=,pgid=,command="]))
            if s["pid"] in current_pids:
                raise Hold("daemon PID still alive after bootout; manual hold")
        os.rename(source,target)
        if source.exists() or digest(target.read_bytes())!=s["registration_sha256"]:
            raise Hold("archive verification failed; manual hold")
        log({"event":"archived","label":label,"sha256":s["registration_sha256"]})
    final = inventory(home)
    if final["targets"] or sorted(x["label"] for x in final["services"].values())!=[KEEP]:
        raise Hold("post-containment registrations not exclusive")
    log({"event":"verified_single_v5_owner","keeper":KEEP})
    print(json.dumps({"status":"contained_for_v6_promotion","keeper":KEEP,"archived":fresh["targets"],"audit_log":str(report_path)},indent=2))

def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--contain",metavar="PREFLIGHT_FINGERPRINT",help="operator-only, guarded and irreversible until separately restored")
    cli.add_argument("--inspect",action="store_true",help="read-only preflight (default)")
    args = cli.parse_args()
    if sys.platform != "darwin":
        print("HOLD: run from an actual macOS Terminal, not from Linux or a bridge request",file=sys.stderr)
        return 2
    try:
        home=Path.home()
        result=inventory(home)
        if args.contain:
            containment(result,home,args.contain)
        else:
            print(json.dumps(result,indent=2,sort_keys=True))
            if result["holds"]:
                print("HOLD: containment currently blocked; no mutation performed",file=sys.stderr)
                return 3
    except (Hold,OSError,ValueError,subprocess.TimeoutExpired,plistlib.InvalidFileException) as exc:
        print("HOLD: "+str(exc),file=sys.stderr)
        return 4
    return 0

if __name__=="__main__":
    raise SystemExit(main())
