"""Minimal JSON CLI for the local harness protocol."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .daemon import request, serve_forever
from .runtime import socket_path


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="llm-git-harness")
    sub = p.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the isolated harness Unix-socket service")
    serve.add_argument("--db")
    serve.add_argument("--socket")
    call = sub.add_parser("call", help="send one JSON protocol request")
    call.add_argument("--socket", default=str(socket_path()))
    call.add_argument("message", nargs="?", help="JSON object; stdin is used when omitted")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "serve":
        serve_forever(db=args.db, sock=args.socket)
        return 0
    raw = args.message if args.message is not None else sys.stdin.read()
    try:
        message = json.loads(raw)
        response = request(message, path=Path(args.socket))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": {"type": "client_error", "message": str(exc)}}), file=sys.stderr)
        return 2
    print(json.dumps(response, sort_keys=True, indent=2))
    return 0 if response.get("ok") else 1
