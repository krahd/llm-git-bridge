"""Minimal JSON CLI for the local harness protocol."""
from __future__ import annotations

import argparse
import json
import sys
import secrets
from pathlib import Path

from .daemon import request, serve_forever
from .runtime import socket_path


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
    call = sub.add_parser("call", help="send one JSON protocol request")
    call.add_argument("--socket", default=str(socket_path()))
    call.add_argument("message", nargs="?", help="JSON object; stdin is used when omitted")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "serve":
        serve_forever(db=args.db, sock=args.socket, http_host=args.http_host, http_port=args.http_port)
        return 0
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
