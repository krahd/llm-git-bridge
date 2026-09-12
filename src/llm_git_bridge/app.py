from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shutil
import signal
import sys
import time
from pathlib import Path
from typing import Any

from .core import (
    BridgeError,
    PROTOCOL_VERSION,
    branch_exists,
    branch_token,
    build_registry,
    build_snapshot,
    git,
    load_json,
    process_transaction,
    public_registry,
    repo_state,
    resolve_repo,
    save_json,
    utc_now,
    validate_safe_branch_name,
)
from .transport import RcloneTransport

APP_NAME = "llm-git-bridge"
CONFIG_DIR = Path.home() / ".config" / APP_NAME
STATE_DIR = Path.home() / ".local" / "state" / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
REGISTRY_FILE = STATE_DIR / "registry.json"
PUBLISHED_DIR = STATE_DIR / "published-results"
LABEL = "io.llm-git-bridge.daemon"
REMOTE_ROOT = "v2"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
TX_FILENAME_RE = re.compile(r"[A-Za-z0-9._-]{1,120}\.json")
TX_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,120}")


def default_config() -> dict[str, Any]:
    return {
        "version": 1,
        "transport": {"type": "rclone", "remote": None},
        "roots": [],
        "safe_branch_prefix": "ai/",
        "allow_commit": True,
        "push_enabled_repos": [],
        "poll_interval": 1.0,
        "commands": {},
    }


def load_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return default_config()
    cfg = load_json(CONFIG_FILE)
    base = default_config()
    base.update(cfg)
    if isinstance(cfg.get("transport"), dict):
        base["transport"].update(cfg["transport"])
    return base


def save_config(cfg: dict[str, Any]) -> None:
    save_json(CONFIG_FILE, cfg)


def detect_rclone_remote() -> str:
    if shutil.which("rclone") is None:
        raise BridgeError("rclone is not installed")
    from .core import run

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
    return RcloneTransport(str(remote))


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


def materialize(cfg: dict[str, Any], repo_ref: str, *, branch_snapshot: tuple[Path, str] | None = None) -> str:
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
    return remote_rel


def _published_marker(filename: str) -> Path:
    return PUBLISHED_DIR / filename


def _mark_published(filename: str, *, source: str) -> None:
    save_json(_published_marker(filename), {"filename": filename, "published_at": utc_now(), "source": source})


def _validate_request_identity(obj: dict[str, Any], filename: str) -> str:
    if obj.get("protocol") != PROTOCOL_VERSION:
        raise BridgeError(f"unsupported protocol: {obj.get('protocol')!r}")
    txid = obj.get("transaction_id")
    if not isinstance(txid, str) or not TX_ID_RE.fullmatch(txid):
        raise BridgeError("invalid transaction_id")
    if f"{txid}.json" != filename:
        raise BridgeError("transaction_id does not match filename")
    return txid


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


def _process_materialize_request(cfg: dict[str, Any], registry: dict[str, Any], obj: dict[str, Any], filename: str) -> dict[str, Any]:
    txid = _validate_request_identity(obj, filename)
    repo_ref = obj.get("repo")
    if not isinstance(repo_ref, str) or not repo_ref.strip():
        raise BridgeError("materialize request is missing repo")
    repo_id, entry = resolve_repo(registry, repo_ref)
    branch = obj.get("branch")

    if branch is None:
        registry, repo_id, entry = refresh_repo_entry(cfg, repo_id, publish=True)
        snapshot_rel = materialize(cfg, repo_id)
        head = entry["head"]
        branch_name = entry["branch"]
    else:
        branch_name = validate_safe_branch_name(branch, str(cfg.get("safe_branch_prefix", "ai/")))
        repo_path = Path(entry["path"])
        if not branch_exists(repo_path, branch_name):
            raise BridgeError(f"unknown local branch: {branch_name}")

        wt = STATE_DIR / "materialize-worktrees" / txid
        git(repo_path, "worktree", "remove", "--force", str(wt), check=False, timeout=120)
        if wt.exists():
            shutil.rmtree(wt, ignore_errors=True)
        try:
            git(
                repo_path,
                "-c",
                "core.hooksPath=/dev/null",
                "worktree",
                "add",
                "--detach",
                str(wt),
                branch_name,
                timeout=120,
            )
            head = git(wt, "rev-parse", "HEAD", timeout=30).stdout.strip()
            snapshot_rel = materialize(cfg, repo_id, branch_snapshot=(wt, branch_name))
        finally:
            git(repo_path, "worktree", "remove", "--force", str(wt), check=False, timeout=120)
            if wt.exists():
                shutil.rmtree(wt, ignore_errors=True)
            git(repo_path, "worktree", "prune", check=False, timeout=60)

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
    # Observability must never alter transaction or replay semantics.
    try:
        path = STATE_DIR / "metrics.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
    except OSError:
        pass


def _publish_local_result(transport: RcloneTransport, filename: str, local_result: Path) -> float:
    result = load_json(local_result)
    started = time.monotonic()
    transport.upload_json(
        f"{REMOTE_ROOT}/results/{filename}",
        result,
        STATE_DIR / "outbox" / f"result-{filename}",
    )
    elapsed = round(time.monotonic() - started, 4)
    _mark_published(filename, source="local-result")
    _append_metric({
        "event": "result-republish",
        "transaction_id": filename[:-5],
        "result_upload_s": elapsed,
        "recorded_at": utc_now(),
    })
    return elapsed


def reconcile_remote_results(cfg: dict[str, Any]) -> int:
    """Rebuild local publication markers once at process startup.

    This keeps replay protection after local-state loss without putting a remote
    result-directory listing on the normal first-seen transaction hot path.
    """
    transport = transport_from_config(cfg)
    started = time.monotonic()
    remote_results = [
        name
        for name in transport.list_files(f"{REMOTE_ROOT}/results")
        if name.endswith(".json") and TX_FILENAME_RE.fullmatch(name)
    ]
    list_elapsed = round(time.monotonic() - started, 4)
    marked = 0
    for filename in remote_results:
        marker = _published_marker(filename)
        if marker.exists():
            continue
        _mark_published(filename, source="startup-remote-result")
        marked += 1
    _append_metric({
        "event": "startup-reconcile",
        "remote_results": len(remote_results),
        "markers_added": marked,
        "results_list_s": list_elapsed,
        "recorded_at": utc_now(),
    })
    return marked


def process_pending_once(cfg: dict[str, Any]) -> int:
    transport = transport_from_config(cfg)
    poll_started = time.monotonic()
    list_started = time.monotonic()
    tx_files = [
        name
        for name in transport.list_files(f"{REMOTE_ROOT}/transactions")
        if name.endswith(".json") and TX_FILENAME_RE.fullmatch(name)
    ]
    transaction_list_s = round(time.monotonic() - list_started, 4)

    # Normal polling lists only transactions. Replay recovery against the remote
    # result directory is performed once at watcher startup instead of here.
    candidates = [name for name in tx_files if not _published_marker(name).exists()]
    if not candidates:
        return 0

    processed = 0
    still_pending: list[str] = []
    for filename in candidates:
        local_result = STATE_DIR / "results" / filename
        if local_result.exists():
            _publish_local_result(transport, filename, local_result)
            processed += 1
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
        transaction_download_s = 0.0
        try:
            download_started = time.monotonic()
            raw = transport.download_text(f"{REMOTE_ROOT}/transactions/{filename}", tx_local)
            transaction_download_s = round(time.monotonic() - download_started, 4)
            obj = json.loads(raw.lstrip("\ufeff"))
            _validate_request_identity(obj, filename)
            kind = obj.get("kind")

            if kind == "materialize":
                result = _process_materialize_request(cfg, registry, obj, filename)
                registry = load_registry()
            elif kind == "diagnostics":
                result = _process_diagnostics_request(obj, filename)
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
                    except Exception as snap_exc:
                        # The Git commit already succeeded. Preserve that success and
                        # report only the secondary snapshot-publication failure.
                        result["snapshot_error"] = str(snap_exc)
                elif outcome.snapshot is not None:
                    result["snapshot_deferred"] = True
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
                "error": str(exc),
            }

        # Include timings knowable before acknowledgement in the durable result.
        # The result-upload duration itself is recorded locally after upload completes.
        result["transport_timings"] = {
            "transaction_list_s": transaction_list_s,
            "transaction_download_s": transaction_download_s,
            "pre_result_upload_s": round(time.monotonic() - request_started, 4),
        }

        # Durable local result first; remote acknowledgement second. If upload fails,
        # the next poll republishes this result rather than re-executing the request.
        save_json(local_result, result)
        upload_started = time.monotonic()
        transport.upload_json(
            f"{REMOTE_ROOT}/results/{filename}",
            result,
            STATE_DIR / "outbox" / f"result-{filename}",
        )
        result_upload_s = round(time.monotonic() - upload_started, 4)
        _mark_published(filename, source="processed")
        _append_metric({
            "event": "transaction",
            "transaction_id": filename[:-5],
            "transaction_list_s": transaction_list_s,
            "transaction_download_s": transaction_download_s,
            "pre_result_upload_s": result["transport_timings"]["pre_result_upload_s"],
            "result_upload_s": result_upload_s,
            "request_total_s": round(time.monotonic() - request_started, 4),
            "poll_total_s": round(time.monotonic() - poll_started, 4),
            "recorded_at": utc_now(),
        })
        processed += 1
    return processed


def cmd_setup(args: argparse.Namespace) -> int:
    cfg = load_config()
    remote = args.remote or cfg.get("transport", {}).get("remote") or detect_rclone_remote()
    cfg["transport"] = {"type": "rclone", "remote": remote.rstrip(":")}
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


def cmd_watch(args: argparse.Namespace) -> int:
    cfg = load_config()
    if args.once:
        reconcile_remote_results(cfg)
        count = process_pending_once(cfg)
        print(f"processed: {count}")
        return 0
    interval = float(args.interval or cfg.get("poll_interval", 1.0))
    interval = max(0.5, interval)
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
            if not reconciled:
                marked = reconcile_remote_results(current_cfg)
                reconciled = True
                if marked:
                    print(f"startup reconciliation: {marked} marker(s)", flush=True)
            count = process_pending_once(current_cfg)
            if count:
                print(f"processed: {count}", flush=True)
        except Exception as exc:
            print(f"watch error: {exc}", file=sys.stderr, flush=True)
        deadline = time.monotonic() + interval
        while not stop and time.monotonic() < deadline:
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
    return 0


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
    if args.action == "restart":
        if not PLIST_PATH.exists():
            raise BridgeError("daemon is not installed")
        _launchctl("kickstart", "-k", f"{domain}/{LABEL}")
        print("daemon restarted")
        return 0
    raise BridgeError(f"unknown daemon action: {args.action}")


def cmd_configure_command(args: argparse.Namespace) -> int:
    cfg = load_config()
    registry = load_registry()
    repo_id, _entry = resolve_repo(registry, args.repo)
    commands = cfg.setdefault("commands", {}).setdefault(repo_id, {})
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
    s.add_argument("action", choices=["install", "uninstall", "restart"])
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
