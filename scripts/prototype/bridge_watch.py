#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

DEFAULT_CONFIG = Path.home() / ".config" / "chatgpt-git-bridge" / "repos" / "bridge-test.json"
STATE_ROOT = Path.home() / ".local" / "state" / "chatgpt-git-bridge"

def run(cmd, *, cwd=None, capture=True, check=True):
    p = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if check and p.returncode != 0:
        msg = p.stderr.strip() if capture and p.stderr else f"command failed: {cmd}"
        raise RuntimeError(msg)
    return p

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))

def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def remote(config):
    return f"{config['rclone_remote']}:"

def list_transaction_dirs(config):
    p = run(["rclone", "lsf", f"{remote(config)}transactions", "--dirs-only"], check=False)
    if p.returncode != 0:
        return []
    return sorted(x.rstrip("/") for x in p.stdout.splitlines() if x.strip())

def remote_file_exists(config, remote_rel):
    p = run(["rclone", "lsjson", f"{remote(config)}{remote_rel}"], check=False)
    return p.returncode == 0 and bool(p.stdout.strip())

def result_exists(config, txid):
    return remote_file_exists(config, f"results/{txid}.json")

def transaction_ready(config, txid):
    return (
        remote_file_exists(config, f"transactions/{txid}/request.json")
        and remote_file_exists(config, f"transactions/{txid}/change.patch")
    )

def copy_from_drive(config, remote_rel, local_path: Path):
    local_path.parent.mkdir(parents=True, exist_ok=True)
    run(["rclone", "copyto", f"{remote(config)}{remote_rel}", str(local_path)])

def copy_to_drive(config, local_path: Path, remote_rel):
    run(["rclone", "copyto", str(local_path), f"{remote(config)}{remote_rel}"])

def git(repo: Path, *args, check=True):
    return run(["git", "-C", str(repo), *args], check=check)

def process_tx(config, txid):
    repo = Path(config["repo_path"]).resolve()
    local_tx = STATE_ROOT / config["repo"] / "transactions" / txid
    local_tx.mkdir(parents=True, exist_ok=True)

    req_path = local_tx / "request.json"
    patch_path = local_tx / "change.patch"
    copy_from_drive(config, f"transactions/{txid}/request.json", req_path)
    copy_from_drive(config, f"transactions/{txid}/change.patch", patch_path)

    req = load_json(req_path)
    result = {
        "protocol": 1,
        "transaction_id": txid,
        "repo": config["repo"],
        "processed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "error",
    }

    try:
        if req.get("protocol") != 1:
            raise RuntimeError("unsupported transaction protocol")
        if req.get("repo") != config["repo"]:
            raise RuntimeError("transaction targets a different repository")

        expected = req["base_sha"]
        actual = git(repo, "rev-parse", "HEAD").stdout.strip()
        if actual != expected:
            raise RuntimeError(f"HEAD mismatch: expected {expected}, found {actual}")

        if git(repo, "diff", "--quiet", check=False).returncode != 0:
            raise RuntimeError("tracked working tree has unstaged modifications")
        if git(repo, "diff", "--cached", "--quiet", check=False).returncode != 0:
            raise RuntimeError("index has staged modifications")

        branch = req.get("branch") or f"ai/{txid}"
        prefix = config.get("allowed_branch_prefix", "ai/")
        if not branch.startswith(prefix):
            raise RuntimeError(f"branch must begin with {prefix!r}")

        wt_root = STATE_ROOT / config["repo"] / "worktrees"
        wt_root.mkdir(parents=True, exist_ok=True)
        wt = wt_root / txid
        if wt.exists():
            shutil.rmtree(wt)

        if git(repo, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode == 0:
            raise RuntimeError(f"branch already exists: {branch}")

        git(repo, "worktree", "add", "-b", branch, str(wt), expected)

        check_apply = run(["git", "-C", str(wt), "apply", "--check", str(patch_path)], check=False)
        if check_apply.returncode != 0:
            raise RuntimeError("git apply --check failed: " + (check_apply.stderr.strip() or check_apply.stdout.strip()))

        run(["git", "-C", str(wt), "apply", str(patch_path)])

        diff_check = run(["git", "-C", str(wt), "diff", "--check"], check=False)
        if diff_check.returncode != 0:
            raise RuntimeError("git diff --check failed: " + (diff_check.stderr.strip() or diff_check.stdout.strip()))

        run_requests = req.get("run", [])
        if not isinstance(run_requests, list):
            raise RuntimeError("'run' must be a list")

        allowed_commands = config.get("commands", {})
        command_results = []
        for name in run_requests:
            if name not in allowed_commands:
                raise RuntimeError(f"requested command is not configured locally: {name}")
            cmd = allowed_commands[name]
            if not isinstance(cmd, list) or not all(isinstance(x, str) for x in cmd):
                raise RuntimeError(f"configured command {name!r} must be a JSON argv list")
            p = run(cmd, cwd=wt, check=False)
            command_results.append({
                "name": name,
                "returncode": p.returncode,
                "stdout": p.stdout[-12000:],
                "stderr": p.stderr[-12000:],
            })
            if p.returncode != 0:
                raise RuntimeError(f"configured command failed: {name}")

        commit = None
        if config.get("allow_commit", False):
            run(["git", "-C", str(wt), "add", "-A"])
            status = run(["git", "-C", str(wt), "status", "--porcelain"]).stdout
            if not status.strip():
                raise RuntimeError("patch produced no changes")
            message = req.get("commit_message") or f"Apply ChatGPT bridge transaction {txid}"
            run(["git", "-C", str(wt), "commit", "-m", message])
            commit = run(["git", "-C", str(wt), "rev-parse", "HEAD"]).stdout.strip()

        if config.get("allow_push", False):
            raise RuntimeError("allow_push=true is intentionally unsupported in bridge_watch.py v0.1")

        result.update({
            "status": "success",
            "base_sha": expected,
            "branch": branch,
            "commit": commit,
            "worktree": str(wt),
            "commands": command_results,
        })

    except Exception as e:
        result["error"] = str(e)

    result_path = local_tx / "result.json"
    save_json(result_path, result)
    copy_to_drive(config, result_path, f"results/{txid}.json")
    print(json.dumps(result, indent=2, sort_keys=True))
    return result["status"] == "success"

def run_once(config):
    seen = False
    ok = True
    for txid in list_transaction_dirs(config):
        if result_exists(config, txid):
            continue
        if not transaction_ready(config, txid):
            continue
        seen = True
        if not process_tx(config, txid):
            ok = False
    if not seen:
        print("[bridge] no pending transactions")
    return ok

def main():
    ap = argparse.ArgumentParser(description="Minimal ChatGPT Git bridge transaction watcher")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=10)
    args = ap.parse_args()

    if shutil.which("rclone") is None:
        raise SystemExit("rclone not found")
    if shutil.which("git") is None:
        raise SystemExit("git not found")

    config_path = Path(args.config).expanduser()
    if not config_path.exists():
        raise SystemExit(f"config not found: {config_path}")
    config = load_json(config_path)

    if args.once:
        raise SystemExit(0 if run_once(config) else 1)

    print(f"[bridge] watching every {args.interval}s; Ctrl-C to stop")
    try:
        while True:
            run_once(config)
            time.sleep(max(2, args.interval))
    except KeyboardInterrupt:
        print("\n[bridge] stopped")

if __name__ == "__main__":
    main()
