#!/usr/bin/env python3
"""Migrate the bridge rclone Drive remote to a user-owned OAuth client.

This helper intentionally installs no Google software. Google Cloud project/API
and OAuth-client creation remain browser-side account actions; the local half is
performed with Python's standard library plus the already-installed rclone.
"""
from __future__ import annotations

import argparse
import configparser
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

APP_NAME = "llm-git-bridge"
BRIDGE_CONFIG = Path.home() / ".config" / APP_NAME / "config.json"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "io.llm-git-bridge.daemon.plist"
OAUTH_STATE = Path.home() / ".local" / "state" / APP_NAME / "oauth-setup.json"
PROJECT_ID_RE = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")
CLIENT_ID_RE = re.compile(r"[0-9A-Za-z._-]+\.apps\.googleusercontent\.com")
MUTABLE_RCLONE_KEYS = {"client_id", "client_secret", "token"}
MAX_JSON_BYTES = 2_000_000


class SetupError(RuntimeError):
    pass


def _run(argv: list[str], *, check: bool = True, timeout: float | None = 30) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(argv, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupError(f"command failed to run: {argv[0]}") from exc
    if check and proc.returncode != 0:
        raise SetupError(f"command failed with exit code {proc.returncode}: {argv[0]}")
    return proc


def _load_json_object(path: Path) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise SetupError(f"duplicate JSON key: {key}")
            out[key] = value
        return out

    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            raise SetupError(f"JSON file is unexpectedly large: {path.name}")
        obj = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=reject_duplicates)
    except SetupError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SetupError(f"cannot read JSON file: {path.name}") from exc
    if not isinstance(obj, dict):
        raise SetupError(f"JSON file is not an object: {path.name}")
    return obj


def bridge_remote(config_path: Path = BRIDGE_CONFIG) -> str:
    cfg = _load_json_object(config_path)
    transport = cfg.get("transport")
    if not isinstance(transport, dict) or transport.get("type") != "rclone":
        raise SetupError("bridge transport is not configured as rclone")
    remote = transport.get("remote")
    if not isinstance(remote, str) or not remote.strip():
        raise SetupError("bridge rclone remote is not configured")
    remote = remote.rstrip(":")
    if not re.fullmatch(r"[A-Za-z0-9._ -]{1,100}", remote):
        raise SetupError("configured rclone remote name contains unsupported characters")
    return remote


def validate_desktop_credentials(path: Path) -> tuple[str, str]:
    obj = _load_json_object(path)
    installed = obj.get("installed")
    if not isinstance(installed, dict):
        raise SetupError("OAuth JSON is not a Google Desktop app credential file")
    client_id = installed.get("client_id")
    client_secret = installed.get("client_secret")
    auth_uri = installed.get("auth_uri")
    token_uri = installed.get("token_uri")
    if not isinstance(client_id, str) or not CLIENT_ID_RE.fullmatch(client_id):
        raise SetupError("OAuth JSON has an invalid Desktop client_id")
    if (
        not isinstance(client_secret, str)
        or not client_secret
        or client_secret != client_secret.strip()
        or len(client_secret) > 512
        or any(char in client_secret for char in "\r\n\x00")
    ):
        raise SetupError("OAuth JSON has no client_secret")
    if auth_uri != "https://accounts.google.com/o/oauth2/auth":
        raise SetupError("OAuth JSON has an unexpected auth_uri")
    if token_uri != "https://oauth2.googleapis.com/token":
        raise SetupError("OAuth JSON has an unexpected token_uri")
    return client_id, client_secret


def credential_project_id(path: Path) -> str | None:
    installed = _load_json_object(path).get("installed")
    if not isinstance(installed, dict):
        return None
    project_id = installed.get("project_id")
    return project_id if isinstance(project_id, str) and project_id else None


def _save_prepared_project(project_id: str) -> None:
    OAUTH_STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(OAUTH_STATE.parent, 0o700)
    except OSError:
        pass
    fd, tmp_name = tempfile.mkstemp(prefix=OAUTH_STATE.name + ".", dir=str(OAUTH_STATE.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"project_id": project_id, "prepared_at": time.time()}, fh, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, OAUTH_STATE)
    finally:
        tmp.unlink(missing_ok=True)


def _prepared_project_id() -> str | None:
    if not OAUTH_STATE.exists():
        return None
    try:
        obj = _load_json_object(OAUTH_STATE)
    except SetupError:
        return None
    value = obj.get("project_id")
    return value if isinstance(value, str) and PROJECT_ID_RE.fullmatch(value) else None


def find_credentials(
    downloads: Path | None = None,
    *,
    max_age_hours: float = 24.0,
    expected_project_id: str | None = None,
) -> Path:
    downloads = downloads or (Path.home() / "Downloads")
    candidates: list[Path] = []
    for pattern in ("client_secret_*.json", "client_secret*.json", "credentials.json"):
        candidates.extend(downloads.glob(pattern))
    now = time.time()
    valid: list[Path] = []
    for path in set(candidates):
        try:
            if not path.is_file() or path.is_symlink():
                continue
            if now - path.stat().st_mtime > max_age_hours * 3600:
                continue
            validate_desktop_credentials(path)
            if expected_project_id and credential_project_id(path) != expected_project_id:
                continue
        except (OSError, SetupError):
            continue
        valid.append(path)
    if not valid:
        raise SetupError("no recent Google Desktop OAuth client_secret_*.json found in Downloads")
    return max(valid, key=lambda p: p.stat().st_mtime)


def rclone_config_path() -> Path:
    proc = subprocess.run(
        ["rclone", "config", "file"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    if proc.returncode != 0:
        raise SetupError("rclone config file lookup failed")
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        raise SetupError("rclone did not report a config file")
    path = Path(lines[-1]).expanduser()
    if not path.exists() or not path.is_file() or path.is_symlink():
        raise SetupError("rclone config path is not a regular file")
    return path


def _parse_rclone_config_text(text: str) -> configparser.RawConfigParser:
    if text.lstrip().startswith("RCLONE_ENCRYPT_V0:"):
        raise SetupError("encrypted rclone configs are not edited automatically")
    parser = configparser.RawConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise SetupError("rclone config is not parseable as plaintext INI") from exc
    return parser


def _section_state(parser: configparser.RawConfigParser, remote: str) -> dict[str, str]:
    if not parser.has_section(remote):
        raise SetupError(f"rclone remote not found: {remote}")
    state = {key: value for key, value in parser.items(remote)}
    if state.get("type") != "drive":
        raise SetupError(f"configured bridge remote is not Google Drive: {remote}")
    return state


def _rewrite_remote_oauth_text(
    text: str,
    remote: str,
    client_id: str,
    client_secret: str,
    token: str | None = None,
) -> str:
    """Edit only OAuth fields in one INI section, preserving all other bytes."""
    lines = text.splitlines(keepends=True)
    header = f"[{remote}]"
    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == header:
            if start is not None:
                raise SetupError("rclone config contains duplicate remote sections")
            start = index + 1
            continue
        if start is not None and stripped.startswith("[") and stripped.endswith("]"):
            end = index
            break
    if start is None:
        raise SetupError(f"rclone remote not found: {remote}")

    key_re = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*[:=]")
    found: set[str] = set()
    rewritten: list[str] = []
    for line in lines[start:end]:
        match = key_re.match(line)
        key = match.group(1).lower() if match else None
        if key == "token" and token is None:
            continue
        if key == "token":
            newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            rewritten.append(f"token = {token}{newline}")
            found.add(key)
            continue
        if key == "client_id":
            newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            rewritten.append(f"client_id = {client_id}{newline}")
            found.add(key)
            continue
        if key == "client_secret":
            newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            rewritten.append(f"client_secret = {client_secret}{newline}")
            found.add(key)
            continue
        rewritten.append(line)

    additions: list[str] = []
    newline = "\n"
    if "client_id" not in found:
        additions.append(f"client_id = {client_id}{newline}")
    if "client_secret" not in found:
        additions.append(f"client_secret = {client_secret}{newline}")
    if token is not None and "token" not in found:
        additions.append(f"token = {token}{newline}")
    if additions and rewritten and not rewritten[-1].endswith(("\n", "\r")):
        rewritten[-1] += newline

    return "".join(lines[:start] + rewritten + additions + lines[end:])


def update_plaintext_rclone_config(
    path: Path,
    remote: str,
    client_id: str,
    client_secret: str,
    *,
    token: str | None = None,
) -> tuple[bytes, dict[str, str]]:
    original = path.read_bytes()
    try:
        text = original.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SetupError("rclone config is not UTF-8 plaintext") from exc
    parser = _parse_rclone_config_text(text)
    before = _section_state(parser, remote)
    rewritten = _rewrite_remote_oauth_text(text, remote, client_id, client_secret, token)
    check_parser = _parse_rclone_config_text(rewritten)
    changed = _section_state(check_parser, remote)
    if changed.get("client_id") != client_id or changed.get("client_secret") != client_secret:
        raise SetupError("failed to prepare rclone OAuth fields")
    if token is None and "token" in changed:
        raise SetupError("failed to remove the old rclone OAuth token")
    if token is not None and changed.get("token") != token:
        raise SetupError("failed to install the new rclone OAuth token")

    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(rewritten)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return original, before


def _acquire_config_lock(config_path: Path):
    """Serialize OAuth migrations touching the same rclone configuration."""
    import fcntl

    lock_path = config_path.with_name(config_path.name + ".llm-git-bridge.lock")
    fh = lock_path.open("a+", encoding="utf-8")
    try:
        os.chmod(lock_path, 0o600)
    except OSError:
        pass
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        fh.close()
        raise SetupError("another OAuth migration is already using this rclone config") from exc
    return fh


def _release_config_lock(fh) -> None:
    import fcntl

    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


def _cleanup_oauth_temp_dirs(config_path: Path) -> None:
    """Remove private helper directories left by a previous interrupted run."""
    for child in config_path.parent.glob(".llm-git-bridge-oauth-*"):
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
        except OSError:
            # A stale private directory is not a reason to risk deleting a path we
            # cannot inspect. The new migration uses a fresh unique directory.
            continue


def create_temporary_remote_config(
    source_path: Path,
    remote: str,
    client_id: str,
    client_secret: str,
) -> tuple[Path, dict[str, str]]:
    """Create a private one-remote config for the interactive reconnect.

    The authoritative rclone config remains untouched until OAuth and mailbox
    validation succeed. This also prevents rclone's config writer from
    reformatting or otherwise modifying unrelated remotes/comments.
    """
    text = source_path.read_text(encoding="utf-8")
    parser = _parse_rclone_config_text(text)
    before = _section_state(parser, remote)

    temporary = configparser.RawConfigParser(interpolation=None)
    temporary.add_section(remote)
    for key, value in before.items():
        if key in MUTABLE_RCLONE_KEYS:
            continue
        temporary.set(remote, key, value)
    temporary.set(remote, "client_id", client_id)
    temporary.set(remote, "client_secret", client_secret)

    workdir = Path(tempfile.mkdtemp(prefix=".llm-git-bridge-oauth-", dir=str(source_path.parent)))
    try:
        os.chmod(workdir, 0o700)
    except OSError:
        pass
    tmp = workdir / "rclone.conf"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            temporary.write(fh, space_around_delimiters=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    return tmp, before


def oauth_token_from_config(path: Path, remote: str) -> str:
    parser = _parse_rclone_config_text(path.read_text(encoding="utf-8"))
    state = _section_state(parser, remote)
    token = state.get("token", "").strip()
    if (
        not token
        or len(token) > 100_000
        or any(char in token for char in "\r\n\x00")
    ):
        raise SetupError("rclone reconnect produced an invalid OAuth token")
    try:
        token_obj = json.loads(token)
    except json.JSONDecodeError as exc:
        raise SetupError("rclone reconnect produced an invalid OAuth token") from exc
    if not isinstance(token_obj, dict) or not isinstance(token_obj.get("access_token"), str):
        raise SetupError("rclone reconnect produced an invalid OAuth token")
    return token


def restore_bytes(path: Path, original: bytes) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".restore.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(original)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def verify_rclone_config(
    path: Path,
    remote: str,
    client_id: str,
    client_secret: str,
    before: dict[str, str],
) -> None:
    text = path.read_text(encoding="utf-8")
    parser = _parse_rclone_config_text(text)
    after = _section_state(parser, remote)
    if after.get("client_id") != client_id:
        raise SetupError("rclone client_id did not persist")
    if after.get("client_secret") != client_secret:
        raise SetupError("rclone client_secret did not persist")
    if not after.get("token"):
        raise SetupError("rclone reconnect did not produce an OAuth token")
    before_fixed = {k: v for k, v in before.items() if k not in MUTABLE_RCLONE_KEYS}
    after_fixed = {k: v for k, v in after.items() if k not in MUTABLE_RCLONE_KEYS}
    if before_fixed != after_fixed:
        raise SetupError("rclone reconnect changed non-OAuth remote settings; refusing migration")


def validate_mailbox(remote: str, config_path: Path) -> None:
    common = ["rclone", "--config", str(config_path), "lsf"]
    checks = [
        ([*common, f"{remote}:v2/meta", "--files-only", "--max-depth", "1"], "repos.json"),
        ([*common, f"{remote}:v2/transactions", "--files-only", "--max-depth", "1"], None),
    ]
    for argv, required in checks:
        proc = subprocess.run(
            argv,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )
        if proc.returncode != 0:
            raise SetupError("rclone could not access the existing bridge mailbox")
        if required and required not in proc.stdout.splitlines():
            raise SetupError("existing bridge mailbox was not found under the configured root")


def _assert_watcher_stopped(timeout: float = 5.0) -> None:
    """Wait until the bridge watcher releases its singleton lock."""
    import fcntl

    lock_path = OAUTH_STATE.parent / "watch.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fh = lock_path.open("a+", encoding="utf-8")
    deadline = time.monotonic() + max(0.1, timeout)
    try:
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise SetupError("bridge watcher is still running; refusing to edit rclone credentials")
                time.sleep(0.1)
    finally:
        fh.close()


def _daemon_command(action: str) -> None:
    root = Path(__file__).resolve().parents[1]
    wrapper = root / "bin" / "llm-git-bridge"
    _run([str(wrapper), "daemon", action], timeout=30)


def _daemon_is_running() -> bool:
    if not PLIST_PATH.exists() or sys.platform != "darwin":
        return False
    try:
        proc = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/io.llm-git-bridge.daemon"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def prepare(project_id: str | None) -> int:
    if shutil.which("rclone") is None:
        raise SetupError("rclone is not installed")
    remote = bridge_remote()
    print(f"Bridge remote: {remote}:")
    print("No Google software will be installed locally.")
    if project_id is None:
        url = "https://console.cloud.google.com/projectcreate"
        webbrowser.open(url)
        print("Opened Google Cloud project creation in your browser.")
        print("After creating/selecting a project, rerun:")
        print("  python3 scripts/google_drive_oauth.py prepare --project-id YOUR_PROJECT_ID")
        return 0
    if not PROJECT_ID_RE.fullmatch(project_id):
        raise SetupError("project ID does not look like a valid Google Cloud project ID")
    _save_prepared_project(project_id)
    q = urllib.parse.urlencode({"project": project_id})
    urls = [
        f"https://console.cloud.google.com/apis/library/drive.googleapis.com?{q}",
        f"https://console.cloud.google.com/auth/branding?{q}",
        f"https://console.cloud.google.com/auth/audience?{q}",
        f"https://console.cloud.google.com/auth/dataaccess?{q}",
        f"https://console.cloud.google.com/auth/clients?{q}",
    ]
    for url in urls:
        webbrowser.open(url)
    print("Opened the exact Google Cloud browser pages for the selected project.")
    print("Manual Google-side actions remaining:")
    print("  1. Enable Google Drive API.")
    print("  2. Configure Branding/Audience. Use Internal only if the project and Drive account are in the same Workspace organisation; otherwise External.")
    print("  3. For External, add your Drive account as a test user and add https://www.googleapis.com/auth/drive under Data Access.")
    print("  4. Create an OAuth client of type Desktop app and download its JSON.")
    print("  5. For durable External use, publish the app rather than leaving it in Testing.")
    print("Then run: python3 scripts/google_drive_oauth.py finish")
    return 0


def finish(credentials: Path | None, *, keep_credentials: bool) -> int:
    if shutil.which("rclone") is None:
        raise SetupError("rclone is not installed")
    remote = bridge_remote()
    expected_project_id = _prepared_project_id()
    cred_path = (
        credentials.expanduser()
        if credentials
        else find_credentials(expected_project_id=expected_project_id)
    )
    if expected_project_id and credential_project_id(cred_path) != expected_project_id:
        raise SetupError("OAuth credential file belongs to a different Google Cloud project")
    if cred_path.is_symlink() or not cred_path.is_file():
        raise SetupError("credentials path is not a regular file")
    client_id, client_secret = validate_desktop_credentials(cred_path)
    try:
        os.chmod(cred_path, 0o600)
    except OSError:
        pass

    config_path = rclone_config_path()
    config_lock = _acquire_config_lock(config_path)
    _cleanup_oauth_temp_dirs(config_path)
    original: bytes | None = None
    daemon_was_running = _daemon_is_running()
    stopped = False
    success = False
    temporary_config: Path | None = None
    migration_error: Exception | None = None
    restart_error: SetupError | None = None
    try:
        if daemon_was_running:
            _daemon_command("stop")
            stopped = True
        _assert_watcher_stopped()

        original = config_path.read_bytes()
        temporary_config, before = create_temporary_remote_config(
            config_path,
            remote,
            client_id,
            client_secret,
        )
        print(f"Prepared OAuth client credentials for {remote}: without exposing the secret on a command line.")
        print("A browser authorization step is now required by Google OAuth.")
        _run(
            [
                "rclone",
                "--config",
                str(temporary_config),
                "config",
                "reconnect",
                f"{remote}:",
                "--auto-confirm",
            ],
            timeout=None,
        )

        verify_rclone_config(temporary_config, remote, client_id, client_secret, before)
        validate_mailbox(remote, temporary_config)
        new_token = oauth_token_from_config(temporary_config, remote)

        _original_again, _before_again = update_plaintext_rclone_config(
            config_path,
            remote,
            client_id,
            client_secret,
            token=new_token,
        )
        verify_rclone_config(config_path, remote, client_id, client_secret, before)
        success = True
    except KeyboardInterrupt:
        migration_error = SetupError("OAuth migration cancelled; original rclone config restored")
        if original is not None:
            restore_bytes(config_path, original)
    except Exception as exc:
        migration_error = exc
        if original is not None:
            restore_bytes(config_path, original)
    finally:
        if temporary_config is not None:
            shutil.rmtree(temporary_config.parent, ignore_errors=True)
        if daemon_was_running and stopped:
            try:
                _daemon_command("start")
            except SetupError as exc:
                restart_error = exc
        if success and not keep_credentials:
            cred_path.unlink(missing_ok=True)
        _release_config_lock(config_lock)

    if migration_error is not None:
        if restart_error is not None:
            raise SetupError("OAuth migration failed; original config was restored, but daemon restart also failed") from migration_error
        raise migration_error
    if restart_error is not None:
        raise SetupError("OAuth migration succeeded, but the bridge daemon could not be restarted") from restart_error

    print("OAuth migration validated against the existing mailbox.")
    if not keep_credentials:
        print(f"Removed downloaded credential file: {cred_path.name}")
    print("Run the remote doctor check to confirm custom_drive_client_id_configured=true.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    prep = sub.add_parser("prepare", help="open only the unavoidable Google Cloud browser pages")
    prep.add_argument("--project-id")

    done = sub.add_parser("finish", help="consume the downloaded Desktop OAuth JSON and migrate rclone")
    done.add_argument("--credentials", type=Path)
    done.add_argument("--keep-credentials", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            return prepare(args.project_id)
        if args.command == "finish":
            return finish(args.credentials, keep_credentials=args.keep_credentials)
        raise SetupError("unknown command")
    except SetupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
