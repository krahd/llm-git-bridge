#!/usr/bin/env python3
"""Single-owner consolidation of Local Executor Bridge v6 on one Mac.

Run ONLY from an interactive operator context on the Mac itself, never as a
request through any bridge mailbox. Subcommands:

  census                 read-only inventory of every bridge registration,
                         process, lock holder, journal, approval and queue.
  migrate --plan P --fingerprint F
                         stop and retire every bridge claimant, install the
                         staged, manifest-verified release as the one
                         production LaunchAgent and verify exclusive ownership.
  rollback --run DIR     reverse the recorded filesystem actions of a run whose
                         new owner never admitted a request (operator decision).

The migration never deletes journals, approvals, configs, binaries or Git
state: registrations and superseded install directories are archived under
the run directory. Historical indeterminate receipts are listed in a
quarantine ledger; they are terminal and are never replayed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import plistlib
import re
import shlex
import stat
import subprocess
import sys
import time

LABEL_RE = re.compile(
    r"(?:com\.tom\.chatgpt-shell-bridge(?:[-.][A-Za-z0-9._-]+)?"
    r"|io\.llm-git-bridge\.[A-Za-z0-9._-]+"
    r"|net\.laurenzo\.local-executor-bridge(?:[-.][A-Za-z0-9._-]+)?)\Z")
DAEMON_RE = re.compile(r"(?:^|/)bridge\.py\s+daemon(?:\s|$)")
APPROVAL_APP_RE = re.compile(r"/[^/]+\.app/Contents/MacOS/local-executor-approval$")
ID_RE = re.compile(r"[A-Za-z0-9_-]{8,128}\Z")
MANIFEST_FILES = {
    "bridge_sha256": "bridge.py",
    "workspace_sha256": "workspace.py",
    "approval_helper_sha256": "approval_helper.py",
    "trusted_operations_sha256": "trusted_operations.py",
    "bridge_mailbox_inspector_sha256": "bridge_mailbox_inspector.py",
    "ssh_pinned_helper_sha256": "ssh_pinned_helper.py",
    "approval_app_info_sha256": "Local Executor Approval.app/Contents/Info.plist",
    "approval_app_executable_sha256":
        "Local Executor Approval.app/Contents/MacOS/local-executor-approval",
}
STOP_WAIT_SECONDS = 60
HEALTH_WAIT_SECONDS = 120


class Hold(RuntimeError):
    """A fail-closed stop: nothing further may be changed automatically."""


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256(path.read_bytes())


class Host:
    """All interaction with the OS. Tests substitute a fake."""

    uid = os.getuid()

    def run(self, argv: list[str], timeout: int = 30, check: bool = True) -> str:
        cp = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        if check and cp.returncode != 0:
            raise Hold(f"command failed: {argv[0]} {argv[1] if len(argv) > 1 else ''} "
                       f"rc={cp.returncode}")
        return cp.stdout

    def launchctl_list(self) -> dict[str, int | None]:
        out = {}
        for line in self.run(["/bin/launchctl", "list"]).splitlines():
            fields = line.split(None, 2)
            if len(fields) == 3 and LABEL_RE.fullmatch(fields[2]):
                out[fields[2]] = int(fields[0]) if fields[0].isdecimal() else None
        return out

    def disabled_labels(self) -> dict[str, bool]:
        out = {}
        raw = self.run(["/bin/launchctl", "print-disabled", f"gui/{self.uid}"])
        for match in re.finditer(r'"([^"]+)"\s*=>\s*(disabled|enabled|true|false)', raw):
            if LABEL_RE.fullmatch(match.group(1)):
                out[match.group(1)] = match.group(2) in ("disabled", "true")
        return out

    def processes(self) -> dict[int, tuple[int, int, str]]:
        out = {}
        raw = self.run(["/bin/ps", "-axww", "-o", "pid=,ppid=,pgid=,command="])
        for line in raw.splitlines():
            parts = line.strip().split(None, 3)
            if len(parts) == 4 and all(p.isdigit() for p in parts[:3]):
                out[int(parts[0])] = (int(parts[1]), int(parts[2]), parts[3])
        return out

    def open_lock_files(self, pid: int) -> list[str]:
        raw = self.run(["/usr/sbin/lsof", "-nP", "-Fn", "-p", str(pid)], check=False)
        return sorted({line[1:] for line in raw.splitlines()
                       if line.startswith("n") and line.endswith(".lock")})

    def lock_holders(self, directory: Path) -> list[str]:
        if not directory.is_dir():
            return []
        raw = self.run(["/usr/sbin/lsof", "-nP", "-Fpn", "+D", str(directory)], check=False)
        return [line for line in raw.splitlines() if line.startswith("p")]

    def remote_queue(self, remote: str, requests_folder_id: str) -> list[str]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+:", remote) or not ID_RE.fullmatch(requests_folder_id):
            raise Hold("unsafe remote or pinned requests folder identity")
        raw = self.run(["rclone", "--drive-root-folder-id", requests_folder_id,
                        "--timeout", "20s", "lsf", remote, "--files-only",
                        "--max-depth", "1"], timeout=60)
        names = raw.splitlines()
        if len(names) > 4096 or any("/" in n or not n for n in names):
            raise Hold("remote request listing is malformed or unbounded")
        return sorted(n for n in names if n.endswith(".json"))

    def user_temp_dir(self) -> Path:
        # launchd agents inherit the per-user Darwin TMPDIR, which may differ
        # from this operator shell's TMPDIR.
        value = self.run(["/usr/bin/getconf", "DARWIN_USER_TEMP_DIR"]).strip()
        if not value.startswith("/"):
            raise Hold("cannot determine the per-user launchd temporary directory")
        return Path(value)

    def launchctl(self, *args: str, check: bool = True) -> str:
        return self.run(["/bin/launchctl", *args], check=check)

    def signal(self, pid: int, sig: int) -> None:
        os.kill(pid, sig)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def now(self) -> float:
        return time.time()


def _owned_regular(path: Path, limit: int = 1 << 20) -> bytes:
    meta = path.lstat()
    if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid()
            or meta.st_mode & 0o022 or meta.st_size > limit):
        raise Hold(f"unsafe or oversized file: {path}")
    return path.read_bytes()


def _program_arguments(plist: dict) -> list[str]:
    args = plist.get("ProgramArguments")
    if isinstance(args, list) and all(isinstance(a, str) for a in args):
        return args
    program = plist.get("Program")
    return [program] if isinstance(program, str) else []


def _daemon_target(args: list[str]) -> tuple[str | None, str | None]:
    scripts = [i for i, a in enumerate(args) if a.endswith("/bridge.py")]
    if len(scripts) != 1:
        return None, None
    i = scripts[0]
    config = None
    for j, a in enumerate(args):
        if a == "--config" and j + 1 < len(args):
            config = args[j + 1]
        elif a.startswith("--config="):
            config = a.partition("=")[2]
    if i + 1 < len(args) and args[i + 1] == "daemon":
        return args[i], config
    return args[i], None


def _read_config(path: str | None) -> dict:
    if not path:
        return {}
    try:
        cfg = json.loads(_owned_regular(Path(path), 1 << 16))
    except (OSError, ValueError, Hold):
        return {"_unreadable": True}
    keys = ("bridge_instance_id", "drive_root_folder_id", "requests_folder_id",
            "results_folder_id", "remote", "base_path", "state_dir",
            "operator_approval_app")
    return {k: cfg.get(k) for k in keys if k in cfg} if isinstance(cfg, dict) else {"_unreadable": True}


def journal_activity(state_dir: Path, *, ledger: bool = False) -> dict:
    """Bounded scan of live-risk evidence. Never reads command bodies."""
    out = {"entries": 0, "active_markers": [], "started_unfinished": [],
           "indeterminate_finished": 0}
    ids = []
    root = state_dir / "requests"
    if root.is_symlink():
        raise Hold(f"linked journal: {root}")
    if not root.is_dir():
        return out
    for entry in os.scandir(root):
        out["entries"] += 1
        if out["entries"] > 500000:
            raise Hold("journal exceeds bounded census size")
        if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            continue
        folder = Path(entry.path)
        started, finished = folder / "started.json", folder / "finished.json"
        if (folder / "active.json").exists():
            out["active_markers"].append(entry.name)
        if started.exists() and not finished.exists():
            out["started_unfinished"].append(entry.name)
        if finished.is_file() and not finished.is_symlink():
            try:
                raw = finished.read_bytes()
                if json.loads(raw).get("status") == "indeterminate":
                    out["indeterminate_finished"] += 1
                    if ledger:
                        ids.append({"request_id": entry.name, "finished_sha256": sha256(raw)})
            except (OSError, ValueError, AttributeError):
                pass
    out["active_markers"].sort()
    out["started_unfinished"].sort()
    if ledger:
        out["indeterminate_ledger"] = sorted(ids, key=lambda r: r["request_id"])
    return out


def approval_activity(state_dir: Path, now: float) -> dict:
    out = {"pending": 0, "unexpired": [], "unverifiable": []}
    pending = state_dir / "approvals" / "pending"
    if pending.is_symlink():
        raise Hold(f"linked approval directory: {pending}")
    if not pending.is_dir():
        return out
    for path in sorted(pending.iterdir()):
        out["pending"] += 1
        try:
            record = json.loads(_owned_regular(path, 1 << 18))
            expires = float(record["expires_at"])
            if record.get("request_id") != path.stem or not math.isfinite(expires):
                raise ValueError("binding")
            if expires > now:
                out["unexpired"].append(path.stem)
        except (OSError, ValueError, KeyError, TypeError, Hold):
            out["unverifiable"].append(path.name)
    return out


def _is_infrastructure(pid: int, daemon_pid: int, procs: dict) -> bool:
    """Transport polls and the wake lease are not request work.

    rclone calls stay in the daemon's own process group, whereas every request
    command is spawned in a new session; a request that runs rclone therefore
    never matches. The wake lease is the daemon's direct caffeinate child.
    """
    ppid, pgid, cmd = procs[pid]
    try:
        argv0 = shlex.split(cmd)[0]
    except (ValueError, IndexError):
        return False
    if Path(argv0).name == "rclone" and pgid == daemon_pid:
        return True
    return argv0 == "/usr/bin/caffeinate" and ppid == daemon_pid


def work_descendants(pid: int, procs: dict[int, tuple[int, int, str]]) -> list[int]:
    return [c for c in descendants(pid, procs) if not _is_infrastructure(c, pid, procs)]


def descendants(pid: int, procs: dict[int, tuple[int, int, str]]) -> list[int]:
    found: set[int] = set()
    while True:
        nxt = {p for p, (ppid, pgid, _) in procs.items()
               if p != pid and (ppid == pid or ppid in found or pgid == pid)}
        if nxt <= found:
            return sorted(found)
        found |= nxt


def census(home: Path, host: Host, *, queues: bool = True) -> dict:
    now = host.now()
    agents = home / "Library" / "LaunchAgents"
    loaded = host.launchctl_list()
    disabled = host.disabled_labels()
    procs = host.processes()
    registrations = []
    for plist_path in sorted(agents.glob("*.plist")):
        try:
            raw = _owned_regular(plist_path, 1 << 16)
            plist = plistlib.loads(raw)
        except (OSError, ValueError, Hold, plistlib.InvalidFileException):
            if LABEL_RE.fullmatch(plist_path.stem):
                registrations.append({"file": str(plist_path), "label": None,
                                      "problem": "unparseable"})
            continue
        if not isinstance(plist, dict):
            continue
        label = plist.get("Label")
        args = _program_arguments(plist)
        script, config = _daemon_target(args)
        is_bridge = (isinstance(label, str) and LABEL_RE.fullmatch(label)) or (
            script is not None and config is not None)
        if not is_bridge:
            continue
        registrations.append({
            "file": str(plist_path), "label": label if isinstance(label, str) else None,
            "filename_matches_label": plist_path.stem == label,
            "run_at_load": bool(plist.get("RunAtLoad")),
            "keep_alive": bool(plist.get("KeepAlive")),
            "script": script, "config_path": config,
            "script_sha256": sha256_file(Path(script)) if script and Path(script).is_file() else None,
            "config": _read_config(config), "plist_sha256": sha256(raw),
        })
    by_label: dict[str, list[dict]] = {}
    for reg in registrations:
        by_label.setdefault(reg["label"] or "<none>", []).append(reg)
    services = {}
    for label in sorted(set(by_label) | set(loaded)):
        regs = by_label.get(label, [])
        pid = loaded.get(label)
        entry = {"label": label, "registrations": [r["file"] for r in regs],
                 "loaded": label in loaded, "pid": pid,
                 "disabled": disabled.get(label, False),
                 "duplicate_declarations": len(regs) > 1, "children": [],
                 "lock_files": []}
        if pid is not None and pid in procs:
            entry["command"] = procs[pid][2][:400]
            entry["children"] = work_descendants(pid, procs)
            entry["lock_files"] = host.open_lock_files(pid)
        services[label] = entry
    launchd_pids = {s["pid"] for s in services.values() if s["pid"]}
    daemons = {pid: cmd[:400] for pid, (_, _, cmd) in procs.items() if DAEMON_RE.search(cmd)}
    approval_apps = {pid: cmd[:400] for pid, (_, _, cmd) in procs.items()
                     if APPROVAL_APP_RE.search(cmd)}
    states = {}
    mailboxes = {}
    for reg in registrations:
        cfg = reg["config"]
        state = cfg.get("state_dir")
        if isinstance(state, str) and state not in states:
            sp = Path(state).expanduser()
            states[state] = {"journal": journal_activity(sp),
                             "approvals": approval_activity(sp, now)}
        root = cfg.get("drive_root_folder_id")
        if isinstance(root, str):
            key = f"{cfg.get('remote')}|{root}|{cfg.get('requests_folder_id')}"
            mailboxes.setdefault(key, {"remote": cfg.get("remote"), "root": root,
                                       "requests_folder_id": cfg.get("requests_folder_id"),
                                       "labels": []})["labels"].append(reg["label"])
    if queues:
        for box in mailboxes.values():
            try:
                box["pending"] = host.remote_queue(box["remote"], box["requests_folder_id"])
            except Hold as exc:
                box["pending_error"] = str(exc)
    report = {
        "schema": 1, "kind": "bridge_consolidation_census", "observed_at": now,
        "registrations": registrations, "services": services,
        "unregistered_daemons": sorted(p for p in daemons if p not in launchd_pids),
        "daemon_processes": daemons, "approval_apps": approval_apps,
        "states": states, "mailboxes": mailboxes,
    }
    stable = {k: v for k, v in report.items() if k not in ("observed_at",)}
    for state in stable["states"].values():
        state["approvals"] = dict(state["approvals"])
    report["fingerprint"] = sha256(json.dumps(stable, sort_keys=True, default=str).encode())
    return report


def verify_operator_context(host: Host) -> None:
    procs = host.processes()
    pid = os.getpid()
    for _ in range(64):
        if pid <= 1:
            return
        if pid not in procs:
            raise Hold("cannot verify operator process ancestry")
        ppid, _, cmd = procs[pid]
        if DAEMON_RE.search(cmd):
            raise Hold("refusing service management from inside a bridge-executed request")
        pid = ppid
    raise Hold("operator process ancestry is unexpectedly deep")


def verify_release(plan: dict) -> dict:
    release = Path(plan["release_dir"]).expanduser()
    if release.is_symlink() or not release.is_dir():
        raise Hold("release directory missing or symlinked")
    manifest = json.loads(_owned_regular(release / "install-manifest.json", 1 << 16))
    if manifest.get("schema") != 1 or manifest.get("source_commit") != plan["release_commit"]:
        raise Hold("release manifest does not name the planned source commit")
    for key, rel in MANIFEST_FILES.items():
        path = release / rel
        if path.is_symlink() or not path.is_file() or manifest.get(key) != sha256_file(path):
            raise Hold(f"release file does not match manifest: {rel}")
    plist_path = release / "launchagent.plist"
    raw_plist = _owned_regular(plist_path, 1 << 16)
    if manifest.get("launchagent_plist_sha256") != sha256(raw_plist):
        raise Hold("release LaunchAgent does not match manifest")
    plist = plistlib.loads(raw_plist)
    args = _program_arguments(plist)
    script, config = _daemon_target(args)
    if (plist.get("Label") != plan["keeper_label"] or script != str(release / "bridge.py")
            or not config):
        raise Hold("release LaunchAgent label/program does not match the plan")
    raw_cfg = _owned_regular(Path(config), 1 << 16)
    if manifest.get("config_sha256") != sha256(raw_cfg):
        raise Hold("release config does not match manifest")
    cfg = json.loads(raw_cfg)
    expected = {"drive_root_folder_id": plan["root_id"],
                "requests_folder_id": plan["requests_id"],
                "results_folder_id": plan["results_id"],
                "state_dir": plan["state_dir"]}
    for key, value in expected.items():
        if cfg.get(key) != value:
            raise Hold(f"release config {key} differs from the planned production identity")
    env = plist.get("EnvironmentVariables") or {}
    for key in ("LOCAL_EXECUTOR_BRIDGE_STATE_DIR", "CHATGPT_SHELL_BRIDGE_STATE_DIR"):
        if env.get(key) != plan["state_dir"]:
            raise Hold("release LaunchAgent state environment differs from production")
    app = cfg.get("operator_approval_app")
    if app != str(release / "Local Executor Approval.app"):
        raise Hold("release approval app is not the release's own bundle")
    return {"plist_path": plist_path, "plist_raw": raw_plist, "config_path": Path(config),
            "config": cfg, "manifest": manifest}


class RunLog:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.path = run_dir / "events.jsonl"
        self.actions_path = run_dir / "recovery-actions.jsonl"

    @staticmethod
    def _append(path: Path, record: dict) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (json.dumps(record, sort_keys=True, default=str) + "\n").encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def event(self, name: str, **data) -> None:
        self._append(self.path, {"at": time.time(), "event": name, **data})
        print(f"[consolidate] {name} " + json.dumps(data, sort_keys=True, default=str)[:300],
              flush=True)

    def action(self, kind: str, **data) -> None:
        """Record a reversible filesystem action BEFORE relying on it."""
        self._append(self.actions_path, {"at": time.time(), "kind": kind, **data})


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def archive_file(source: Path, dest_dir: Path, log: RunLog) -> Path:
    """Move a file without overwrite: link (no-replace), verify, then unlink."""
    expected = sha256_file(source)
    dest_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = dest_dir / source.name
    if target.exists() or target.is_symlink():
        raise Hold(f"archive target exists: {target}")
    log.action("link_then_unlink", source=str(source), target=str(target), sha256=expected)
    os.link(source, target, follow_symlinks=False)
    if sha256_file(target) != expected:
        raise Hold(f"archived copy unverifiable; original retained: {source}")
    _fsync_dir(dest_dir)
    os.unlink(source)
    _fsync_dir(source.parent)
    return target


def rename_aside(source: Path, target: Path, log: RunLog) -> None:
    if target.exists() or target.is_symlink():
        raise Hold(f"rename target exists: {target}")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    log.action("rename", source=str(source), target=str(target))
    os.rename(source, target)
    _fsync_dir(source.parent)
    _fsync_dir(target.parent)


def wait_until(host: Host, predicate, seconds: float, what: str) -> None:
    deadline = host.now() + seconds
    while True:
        if predicate():
            return
        if host.now() >= deadline:
            raise Hold(f"timed out waiting for {what}")
        host.sleep(0.5)


def _gate_live_work(report: dict, log: RunLog, phase: str, production_state: str) -> None:
    """Hold on anything a stop could abandon or a new owner could misjudge.

    Live children and unexpired approvals block for every service. Journal
    in-flight evidence blocks for the production journal, which the new owner
    inherits; retired journals keep their historical markers as evidence.
    """
    problems = []
    for label, svc in report["services"].items():
        if svc["children"]:
            problems.append(f"{label} has live child processes {svc['children']}")
    if production_state not in report["states"]:
        problems.append("production state directory is not referenced by any registration")
    for state, data in report["states"].items():
        if data["approvals"]["unexpired"]:
            problems.append(f"{state} has actionable approvals {data['approvals']['unexpired']}")
        if state != production_state:
            continue
        if data["journal"]["active_markers"] or data["journal"]["started_unfinished"]:
            problems.append(f"{state} has in-flight or unresolved STARTED requests")
        if data["approvals"]["unverifiable"]:
            problems.append(f"{state} has unverifiable approval records")
    if report["unregistered_daemons"]:
        problems.append(f"unregistered bridge daemons {report['unregistered_daemons']}")
    log.event("gate_live_work", phase=phase, problems=problems)
    if problems:
        raise Hold("; ".join(problems))


def stop_service(label: str, svc: dict, host: Host, log: RunLog, *, disable: bool) -> None:
    unit = f"gui/{host.uid}/{label}"
    if disable:
        host.launchctl("disable", unit)
        log.event("disabled", label=label)
    if svc["loaded"]:
        host.launchctl("bootout", unit, check=False)
        log.event("bootout_requested", label=label, pid=svc["pid"])
    wait_until(host, lambda: label not in host.launchctl_list(), STOP_WAIT_SECONDS,
               f"{label} to unload")
    if svc["pid"]:
        wait_until(host, lambda: svc["pid"] not in host.processes(), STOP_WAIT_SECONDS,
                   f"{label} pid {svc['pid']} to exit")
    log.event("stopped", label=label)


def migrate(plan: dict, fingerprint: str, home: Path, host: Host) -> dict:
    keeper = plan["keeper_label"]
    if not LABEL_RE.fullmatch(keeper):
        raise Hold("keeper label is not a bridge label")
    release = verify_release(plan)
    verify_operator_context(host)
    report = census(home, host)
    if report["fingerprint"] != fingerprint:
        raise Hold("live census changed since operator review; re-run census")
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = home / "Library" / "Application Support" / "llm-git-bridge" / "consolidation" / run_id
    run_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    log = RunLog(run_dir)
    (run_dir / "plan.json").write_text(json.dumps(plan, indent=2, sort_keys=True))
    (run_dir / "census-before.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    log.event("begin", run_dir=str(run_dir), fingerprint=fingerprint, keeper=keeper,
              release_commit=plan["release_commit"])
    # Preserve every registration and configuration byte-for-byte first.
    copies = run_dir / "copies"
    copies.mkdir(mode=0o700)
    for i, reg in enumerate(report["registrations"]):
        dest = copies / f"{i:02d}-{Path(reg['file']).name}"
        dest.write_bytes(Path(reg["file"]).read_bytes())
        if reg.get("config_path") and Path(reg["config_path"]).is_file():
            (copies / f"{i:02d}-config.json").write_bytes(Path(reg["config_path"]).read_bytes())
    _fsync_dir(copies)
    _gate_live_work(report, log, "pre-stop", plan["state_dir"])

    # 1) Labels without a running process first (respawn loops), so that no
    #    claimant can take the mailbox when a running owner later releases it.
    services = report["services"]
    order = sorted(services, key=lambda l: (services[l]["pid"] is not None, l == keeper, l))
    for label in order:
        if label == "<none>":
            continue
        stop_service(label, services[label], host, log, disable=(label != keeper))
    for pid in report["approval_apps"]:
        log.event("stopping_old_approval_app", pid=pid)
        try:
            host.signal(pid, 15)
        except ProcessLookupError:
            pass
        wait_until(host, lambda p=pid: p not in host.processes(), STOP_WAIT_SECONDS,
                   f"approval app {pid} to exit")

    after = census(home, host)
    (run_dir / "census-stopped.json").write_text(json.dumps(after, indent=2, sort_keys=True, default=str))
    if after["daemon_processes"] or after["approval_apps"] or any(
            s["loaded"] for s in after["services"].values()):
        raise Hold("bridge processes or loaded services remain after stop")
    lock_dirs = [Path("/tmp") / f"chatgpt-shell-bridge-{host.uid}",
                 home / ".local" / "state" / "local-executor-bridge-locks"]
    lock_dirs.append(host.user_temp_dir() / f"chatgpt-shell-bridge-{host.uid}")
    for directory in lock_dirs:
        if host.lock_holders(directory):
            raise Hold(f"a process still holds a bridge lock in {directory}")
    _gate_live_work(after, log, "post-stop", plan["state_dir"])
    ledger = journal_activity(Path(plan["state_dir"]), ledger=True)
    (run_dir / "indeterminate-ledger.json").write_text(json.dumps(
        {"state_dir": plan["state_dir"], "note": "terminal historical receipts; never replayed",
         "receipts": ledger["indeterminate_ledger"]}, indent=2, sort_keys=True))
    log.event("ledger_written", indeterminate=ledger["indeterminate_finished"])

    # 2) Retire every registration file out of the auto-start directory.
    retired = run_dir / "retired-launchagents"
    for reg in report["registrations"]:
        source = Path(reg["file"])
        if source.exists():
            archive_file(source, retired, log)
    log.event("registrations_archived", count=len(report["registrations"]))

    # 3) Install the release at the canonical paths clients use.
    canonical = Path(plan["canonical_install_link"]).expanduser()
    releases = canonical.parent / "local-executor-bridge-releases"
    if canonical.is_symlink():
        log.action("remove_symlink", path=str(canonical), target=os.readlink(canonical))
        canonical.unlink()
    elif canonical.exists():
        rename_aside(canonical, releases / f"retired-{run_id}-{canonical.name}", log)
    log.action("create_symlink", path=str(canonical), target=str(release["plist_path"].parent))
    canonical.symlink_to(release["plist_path"].parent)
    for legacy in plan.get("legacy_coordinator_dirs", []):
        legacy_path = Path(legacy).expanduser()
        if legacy_path.exists() or legacy_path.is_symlink():
            rename_aside(legacy_path, releases / f"retired-{run_id}-{legacy_path.name}", log)
        legacy_path.mkdir(mode=0o700)
        log.action("create_dir", path=str(legacy_path))
        shim = legacy_path / "workspace.py"
        log.action("create_symlink", path=str(shim), target=str(canonical / "workspace.py"))
        shim.symlink_to(canonical / "workspace.py")
    live_plist = home / "Library" / "LaunchAgents" / f"{keeper}.plist"
    if live_plist.exists() or live_plist.is_symlink():
        raise Hold("keeper registration still present after archival")
    log.action("create_file", path=str(live_plist))
    fd = os.open(live_plist, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(release["plist_raw"])
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_dir(live_plist.parent)
    log.event("release_installed", canonical=str(canonical))

    # 4) Start exactly one owner. From here on, never restart an old owner.
    host.launchctl("enable", f"gui/{host.uid}/{keeper}")
    log.event("point_of_no_automatic_return")
    host.launchctl("bootstrap", f"gui/{host.uid}", str(live_plist))
    log.event("bootstrapped", label=keeper)
    result = verify_single_owner(plan, home, host, log, run_dir)
    log.event("complete", **result)
    return {"run_dir": str(run_dir), **result}


def verify_single_owner(plan: dict, home: Path, host: Host, log: RunLog | None = None,
                        run_dir: Path | None = None) -> dict:
    keeper = plan["keeper_label"]
    release_dir = Path(plan["release_dir"]).expanduser()
    health_path = Path(plan["state_dir"]) / "health.json"

    def healthy() -> bool:
        loaded = host.launchctl_list()
        pid = loaded.get(keeper)
        if pid is None:
            return False
        try:
            health = json.loads(health_path.read_text())
        except (OSError, ValueError):
            return False
        return (health.get("publisher_pid") == pid
                and health.get("build_source_commit") == plan["release_commit"]
                and health.get("build_code_integrity") == "matches_manifest")
    wait_until(host, healthy, HEALTH_WAIT_SECONDS, "fresh PID-bound release health")
    report = census(home, host, queues=False)
    loaded = {l: s for l, s in report["services"].items() if s["loaded"]}
    if list(loaded) != [keeper]:
        raise Hold(f"loaded bridge services are not exactly the keeper: {sorted(loaded)}")
    pid = loaded[keeper]["pid"]
    if list(report["daemon_processes"]) != [pid]:
        raise Hold("bridge daemon processes are not exactly the keeper's PID")
    argv = shlex.split(report["daemon_processes"][pid])
    if str(release_dir / "bridge.py") not in argv:
        raise Hold("running keeper is not executing the release")
    durable = home / ".local" / "state" / "local-executor-bridge-locks"
    held = [f for f in loaded[keeper]["lock_files"] if f.startswith(str(durable))]
    if not held:
        raise Hold("keeper does not hold the durable mailbox locks")
    for svc_label, svc in report["services"].items():
        if svc_label != keeper and svc["registrations"]:
            raise Hold(f"obsolete registration remains: {svc_label}")
    result = {"keeper_pid": pid, "durable_locks_held": len(held),
              "release_commit": plan["release_commit"]}
    if run_dir is not None:
        (run_dir / "census-after.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    if log is not None:
        log.event("verified_single_owner", **result)
    return result


def rollback(run_dir: Path, home: Path, host: Host) -> None:
    """Reverse recorded filesystem actions. Only when no bridge runs at all."""
    verify_operator_context(host)
    report = census(home, host, queues=False)
    if report["daemon_processes"] or any(s["loaded"] for s in report["services"].values()):
        raise Hold("stop and disable every bridge service before a filesystem rollback")
    plan = json.loads((run_dir / "plan.json").read_text())
    journal = journal_activity(Path(plan["state_dir"]))
    if journal["started_unfinished"] or journal["active_markers"]:
        raise Hold("journal has in-flight evidence; reconcile before rollback")
    actions = [json.loads(l) for l in (run_dir / "recovery-actions.jsonl").read_text().splitlines()]
    log = RunLog(run_dir)
    log.event("rollback_begin", actions=len(actions))
    for action in reversed(actions):
        kind = action["kind"]
        if kind in ("create_symlink", "create_file"):
            path = Path(action["path"])
            if path.is_symlink() or path.is_file():
                path.unlink()
        elif kind == "create_dir":
            Path(action["path"]).rmdir()
        elif kind == "remove_symlink":
            Path(action["path"]).symlink_to(action["target"])
        elif kind == "rename":
            os.rename(action["target"], action["source"])
        elif kind == "link_then_unlink":
            source, target = Path(action["source"]), Path(action["target"])
            if not source.exists():
                os.link(target, source)
        log.event("rollback_step", kind=kind, path=action.get("path") or action.get("source"))
    log.event("rollback_filesystem_restored",
              note="services remain disabled; re-enable and bootstrap ONE owner manually")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("census")
    c.add_argument("--no-queues", action="store_true")
    m = sub.add_parser("migrate")
    m.add_argument("--plan", required=True)
    m.add_argument("--fingerprint", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--plan", required=True)
    r = sub.add_parser("rollback")
    r.add_argument("--run", required=True)
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        print("HOLD: this operator tool only runs on the macOS host", file=sys.stderr)
        return 2
    host, home = Host(), Path.home()
    try:
        if args.cmd == "census":
            print(json.dumps(census(home, host, queues=not args.no_queues),
                             indent=2, sort_keys=True, default=str))
        elif args.cmd == "migrate":
            plan = json.loads(Path(args.plan).read_text())
            print(json.dumps(migrate(plan, args.fingerprint, home, host), indent=2))
        elif args.cmd == "verify":
            plan = json.loads(Path(args.plan).read_text())
            print(json.dumps(verify_single_owner(plan, home, host), indent=2))
        else:
            rollback(Path(args.run), home, host)
    except (Hold, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        print(f"CONSOLIDATION_HOLD: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
