"""Minimal JSON CLI for the local harness protocol."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import secrets
from pathlib import Path
import urllib.error
import urllib.request

from .daemon import request, serve_forever
from .runtime import install_manifest_path, safari_extension_dir, socket_path

DEFAULT_BROWSER_HOST = "127.0.0.1"
DEFAULT_BROWSER_PORT = 47653


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="llm-git-harness")
    sub = p.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the isolated harness Unix-socket service")
    serve.add_argument("--db")
    serve.add_argument("--socket")
    serve.add_argument("--http-host", default="127.0.0.1")
    serve.add_argument("--http-port", type=int, default=47653)
    pair = sub.add_parser("pair", help="create a one-time browser pairing code")
    pair.add_argument("--socket", default=str(socket_path()))
    pair.add_argument("--ttl", type=float, default=300.0)
    browser_status = sub.add_parser("browser-status", help="check browser-pilot readiness without creating a pairing code")
    browser_status.add_argument("--http-host", default=DEFAULT_BROWSER_HOST)
    browser_status.add_argument("--http-port", type=int, default=DEFAULT_BROWSER_PORT)
    browser_status.add_argument("--timeout", type=float, default=1.0)
    call = sub.add_parser("call", help="send one JSON protocol request")
    call.add_argument("--socket", default=str(socket_path()))
    call.add_argument("message", nargs="?", help="JSON object; stdin is used when omitted")
    return p


def _browser_status(host: str, port: int, timeout: float) -> dict[str, object]:
    extension = safari_extension_dir()
    manifest = extension / "manifest.json"
    api_ok = False
    api_error = None
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
            api_ok = (
                response.status == 200
                and body.get("ok") is True
                and body.get("service") == "chatgpt-conversation-harness-v1"
                and body.get("protocol") == 1
            )
            if not api_ok:
                api_error = "unexpected health response"
    except (OSError, urllib.error.URLError, json.JSONDecodeError, UnicodeError) as exc:
        api_error = str(exc)
    staged = extension.is_dir() and manifest.is_file()
    integrity_ok = False
    integrity_error = None
    if staged:
        try:
            install_manifest = json.loads(install_manifest_path().read_text(encoding="utf-8"))
            expected = install_manifest.get("safari_files")
            if not isinstance(expected, dict) or not expected:
                raise ValueError("install manifest has no Safari extension hashes")
            actual: dict[str, str] = {}
            for path in sorted(p for p in extension.rglob("*") if p.is_file()):
                relative = path.relative_to(extension).as_posix()
                actual[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
            integrity_ok = actual == expected
            if not integrity_ok:
                integrity_error = "staged Safari extension differs from install manifest"
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            integrity_error = str(exc)
    return {
        "ready": api_ok and staged and integrity_ok,
        "browser_api": {"ok": api_ok, "url": f"http://{host}:{port}/health", "error": api_error},
        "extension": {
            "staged": staged,
            "integrity_ok": integrity_ok,
            "integrity_error": integrity_error,
            "path": str(extension),
            "manifest": str(manifest),
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "serve":
        serve_forever(db=args.db, sock=args.socket, http_host=args.http_host, http_port=args.http_port)
        return 0
    if args.command == "browser-status":
        status = _browser_status(args.http_host, args.http_port, args.timeout)
        print(json.dumps(status, sort_keys=True, indent=2))
        return 0 if status["ready"] else 1
    if args.command == "pair":
        message={"protocol":1,"request_id":"pair-"+secrets.token_hex(8),"action":"create_pairing_code","args":{"ttl":args.ttl}}
        response=request(message,path=Path(args.socket))
        if not response.get("ok"):
            print(json.dumps(response,sort_keys=True,indent=2),file=sys.stderr); return 1
        print(response["result"]["code"]); return 0
    raw = args.message if args.message is not None else sys.stdin.read()
    try:
        message = json.loads(raw)
        response = request(message, path=Path(args.socket))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": {"type": "client_error", "message": str(exc)}}), file=sys.stderr)
        return 2
    print(json.dumps(response, sort_keys=True, indent=2))
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
